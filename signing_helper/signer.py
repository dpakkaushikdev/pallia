"""PDF digital signing via USB DSC token — standalone helper module.

Adapted from app/services/pdf_signer.py for use in the local signing helper.
Entry point: sign_pdf_bytes(pdf_bytes, pin, sig_box=None) -> signed_bytes

``sig_box`` lets the web app dictate where the visible signature goes
(EEE-Taxi invoices capture it at generation time). Without it the helper
looks for the Pallia Trans "For Pallia / Authorised Signatory" footer text.
"""
from __future__ import annotations

import io
import re
import tempfile
from datetime import datetime
from pathlib import Path

from loguru import logger

PKCS11_LIB = r"C:\Windows\System32\CryptoIDA_pkcs11.dll"

# A4 lower-right authorisation box, in PDF points (origin at bottom-left).
_SIG_BOX_FALLBACK = (337, 141, 580, 198)
_TS_FMT = "%Y.%m.%d %H:%M:%S +05'30'"
_IMG_W, _IMG_H = 440, 92


class TokenNotFound(Exception):
    """USB token not found or driver not loaded."""

class WrongPIN(Exception):
    """Incorrect PIN."""

class SigningError(Exception):
    """Generic signing failure."""


class CertificateSelectionError(SigningError):
    """Token certificates do not identify one usable signing identity."""


def _strip_trailing_non_pdf_data(pdf_bytes: bytes) -> bytes:
    """Drop non-whitespace appended after a structurally valid PDF EOF marker."""
    eof = pdf_bytes.rfind(b"%%EOF")
    if eof < 0:
        return pdf_bytes
    marker_end = eof + len(b"%%EOF")
    trailing = pdf_bytes[marker_end:]
    if not trailing.strip(b"\x00\t\n\x0c\r "):
        return pdf_bytes

    # A real PDF ends with startxref + an offset immediately before %%EOF.
    # This avoids trimming data merely because the bytes %%EOF occur in a file.
    trailer = pdf_bytes[max(0, eof - 128):eof]
    match = re.search(rb"startxref\s+(\d+)\s*$", trailer)
    if not match:
        return pdf_bytes
    xref_offset = int(match.group(1))
    if xref_offset >= eof:
        return pdf_bytes
    xref = pdf_bytes[xref_offset:xref_offset + 32].lstrip()
    if not xref.startswith(b"xref") and not re.match(rb"\d+\s+\d+\s+obj\b", xref):
        return pdf_bytes

    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
        if not reader.pages:
            return pdf_bytes
    except Exception:
        return pdf_bytes

    clean = pdf_bytes[:marker_end]
    clean += b"\n"
    logger.warning("Discarded {} non-whitespace bytes appended after PDF EOF before DSC signing.", len(trailing))
    return clean


# ── Appearance image ──────────────────────────────────────────────────────────

def _load_font(filename: str, size: int):
    from PIL import ImageFont
    for path in [
        f"C:/Windows/Fonts/{filename}",
        f"C:/Windows/Fonts/{filename.lower()}",
        f"C:/Windows/Fonts/{filename.upper()}",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _build_appearance_image(signer_name: str, timestamp_str: str):
    """Render a plain signature with the name on two whole-word lines."""
    try:
        from .appearance import build_appearance_image
    except ImportError:
        from appearance import build_appearance_image
    return build_appearance_image(signer_name, timestamp_str, _load_font)


# ── Signature-zone detection ──────────────────────────────────────────────────

def _find_signature_box(pdf_path: Path, require_anchors: bool = False,
                        single_page_only: bool = True) -> tuple[float, float, float, float]:
    try:
        from pypdf import PdfReader
        try:
            from .footer import find_footer_box
        except ImportError:
            from footer import find_footer_box
        reader = PdfReader(str(pdf_path))
        if require_anchors and single_page_only and len(reader.pages) != 1:
            raise SigningError(
                f"This PDF has {len(reader.pages)} pages. Accounts signing accepts only 1-page PDFs."
            )
        return find_footer_box(reader.pages[0])
    except SigningError:
        raise
    except Exception as exc:
        if require_anchors:
            raise SigningError(str(exc)) from exc
        logger.warning("Sig-zone auto-detection failed ({}); using fallback.", exc)
        return _SIG_BOX_FALLBACK


def inspect_signature_box(pdf_bytes: bytes) -> tuple[float, float, float, float]:
    """Find a Pallia footer signature box or fail instead of using a fallback."""
    pdf_bytes = _strip_trailing_non_pdf_data(pdf_bytes)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "inspect.pdf"
        tmp_path.write_bytes(pdf_bytes)
        return _find_signature_box(tmp_path, require_anchors=True)


# ── CN extraction ─────────────────────────────────────────────────────────────

def _extract_cn(cert) -> str:
    try:
        for rdn in cert.subject.chosen:
            for attr in rdn:
                if attr["type"].native == "common_name":
                    val = str(attr["value"].native).strip()
                    return val.split("(")[0].strip()
    except Exception:
        pass
    try:
        hf = cert.subject.human_friendly
        for part in hf.split(","):
            part = part.strip()
            if part.lower().startswith("common name"):
                return part.split(":", 1)[-1].strip()
    except Exception:
        pass
    return "Authorised Signatory"


# ── Public API ────────────────────────────────────────────────────────────────

def sign_pdf_bytes(
    pdf_bytes: bytes,
    pin: str,
    sig_box: tuple[float, float, float, float] | list[float] | None = None,
    require_signature_anchors: bool = False,
    token_serial: str | None = None,
) -> bytes:
    """Sign PDF bytes using the USB DSC token. Returns signed PDF bytes.

    Args:
        sig_box: optional (x1, y1, x2, y2) in PDF points for the visible stamp.
                 When omitted the footer text is searched for.

    Raises:
        TokenNotFound: USB token not connected.
        WrongPIN: Incorrect PIN.
        SigningError: Any other failure.
    """
    pdf_bytes = _strip_trailing_non_pdf_data(pdf_bytes)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "input.pdf"
        tmp_path.write_bytes(pdf_bytes)
        detected_box = (
            _find_signature_box(tmp_path, require_anchors=True)
            if require_signature_anchors else (
                _find_signature_box(tmp_path, require_anchors=True, single_page_only=False)
                if sig_box is None else None
            )
        )

        try:
            from pyhanko.sign import signers
            from pyhanko.sign.fields import SigFieldSpec
            from pyhanko.sign.pkcs11 import PKCS11Signer
            from pyhanko.sign.signers.pdf_signer import PdfSigner
            from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
            from pyhanko.pdf_utils.images import PdfImage
            from pyhanko.stamp import StaticStampStyle
            try:
                from .token_certificate import select_signing_certificate
                from .token_device import open_token_session
            except ImportError:
                from token_certificate import select_signing_certificate
                from token_device import open_token_session
        except ImportError as exc:
            raise SigningError("pyhanko not installed.") from exc

        try:
            session_ctx = open_token_session(PKCS11_LIB, pin, token_serial)
        except ValueError as exc:
            raise CertificateSelectionError(str(exc)) from exc
        except Exception as exc:
            err = str(exc)
            if any(k in err for k in ("CKR_TOKEN_NOT_PRESENT", "CKR_SLOT_ID_INVALID",
                                       "No module", "CKR_GENERAL_ERROR", "cannot load")):
                raise TokenNotFound("USB token not found. Please plug in the DSC pendrive.") from exc
            if any(k in err for k in ("CKR_PIN_INCORRECT", "CKR_PIN_LOCKED")):
                raise WrongPIN("Incorrect PIN. Please try again.") from exc
            raise SigningError(f"Could not open token session: {exc}") from exc

        try:
            with session_ctx as session:
                try:
                    cert, key_selector, chain = select_signing_certificate(session)
                except ValueError as exc:
                    raise CertificateSelectionError(str(exc)) from exc
                cms_signer = PKCS11Signer(pkcs11_session=session, signing_cert=cert,
                                          ca_chain=chain, **key_selector)
                _ = cms_signer.signing_cert

                try:
                    signer_name = _extract_cn(cms_signer.signing_cert)
                except Exception:
                    signer_name = "Authorised Signatory"

                timestamp_str    = datetime.now().strftime(_TS_FMT)
                appearance_img   = _build_appearance_image(signer_name, timestamp_str)
                stamp_style      = StaticStampStyle(
                    background=PdfImage(appearance_img),
                    border_width=0,
                    background_opacity=1.0,
                )
                if sig_box is not None and len(sig_box) == 4:
                    sig_box = tuple(float(v) for v in sig_box)
                    logger.info("Using signature box from request: {}", sig_box)
                elif detected_box is not None:
                    sig_box = detected_box
                else:
                    sig_box = _find_signature_box(tmp_path)

                pdf_signer_obj = PdfSigner(
                    signature_meta=signers.PdfSignatureMetadata(field_name="AuthorisedSignatory"),
                    signer=cms_signer,
                    stamp_style=stamp_style,
                    new_field_spec=SigFieldSpec(
                        sig_field_name="AuthorisedSignatory",
                        on_page=0,
                        box=sig_box,
                    ),
                )

                with open(tmp_path, "rb") as f:
                    writer = IncrementalPdfFileWriter(f)
                    sig_result = pdf_signer_obj.sign_pdf(writer)
                    return sig_result.getvalue()

        except (TokenNotFound, WrongPIN, SigningError):
            raise
        except Exception as exc:
            err = str(exc)
            if any(k in err for k in ("CKR_PIN_INCORRECT", "CKR_PIN_LOCKED")):
                raise WrongPIN("Incorrect PIN. Please try again.") from exc
            if "CKR_TOKEN_NOT_PRESENT" in err:
                raise TokenNotFound("USB token not found. Please plug in the DSC pendrive.") from exc
            raise SigningError(f"Signing failed: {exc}") from exc
