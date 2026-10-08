"""PDF digital signing via USB DSC token (CryptoID / PKCS#11).

Visual appearance replicates Adobe Acrobat's two-column signature box:
  Left  column — signer name in large bold font
  Right column — "Digitally signed by / Date:" details (same as Adobe)

Raises:
    TokenNotFound  — USB token is not connected / driver not loaded
    WrongPIN       — incorrect PIN entered
    SigningError   — any other signing failure
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

PKCS11_LIB = r"C:\Windows\System32\CryptoIDA_pkcs11.dll"
CERT_LABEL  = "cont_333741d242fc5a8c459f"   # update if cert was renewed
SLOT_NO     = 0

# A4 lower-right authorisation box used when footer anchor detection fails.
# (x1, y1, x2, y2) — PDF points, origin bottom-left of page.
_SIG_BOX_FALLBACK = (337, 141, 580, 198)

# Timestamp format matching Adobe's display: "2026.05.13 14:50:06 +05'30'"
_TS_FMT = "%Y.%m.%d %H:%M:%S +05'30'"

# Rendered image size for the stamp.  Aspect ratio matches SIG_BOX (205 × 93 pts ≈ 2.2:1).
_IMG_W, _IMG_H = 440, 92   # pixels — matches box aspect ratio 223×46 pts ≈ 4.8:1


class TokenNotFound(Exception):
    """USB token not found or driver not loaded."""

class WrongPIN(Exception):
    """Incorrect PIN."""

class SigningError(Exception):
    """Generic signing failure."""


# ── Appearance image ──────────────────────────────────────────────────────────

def _load_font(filename: str, size: int):
    """Load a Windows TrueType font by name; fall back to PIL default."""
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
    from signing_helper.appearance import build_appearance_image
    return build_appearance_image(signer_name, timestamp_str, _load_font)


# ── Signature-zone detection ─────────────────────────────────────────────────

def _find_signature_box(pdf_path: Path) -> tuple[float, float, float, float]:
    """Scan the first page for 'For Pallia Trans' and 'Authorised Signatory' text,
    then return a box (x1, y1, x2, y2) that fits between them.

    Falls back to _SIG_BOX_FALLBACK if either anchor cannot be located or the
    detected gap is too small to hold the stamp (< 20 pts).
    """
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        page = reader.pages[0]
        page_width = float(page.mediabox.width)

        for_pallia_ys: list[float] = []
        authorised_ys: list[float] = []

        def _visit(text: str, cm, tm, font_dict, font_size):
            ctm = cm or (1, 0, 0, 1, 0, 0)
            text_matrix = tm or (1, 0, 0, 1, 0, 0)
            # Compose the current transform and text matrix; summing their
            # translations fails on scaled/flipped invoice templates.
            y = ctm[1] * text_matrix[4] + ctm[3] * text_matrix[5] + ctm[5]
            t = text.strip()
            if not t:
                return
            lower = t.casefold()
            if "for pallia" in lower:
                for_pallia_ys.append(y)
            if "authorised" in lower or "authorized" in lower:
                authorised_ys.append(y)

        page.extract_text(visitor_text=_visit)

        if for_pallia_ys and authorised_ys:
            y_for = max(for_pallia_ys)
            # Select the nearest "Authorised" label below the footer; these
            # forms also have unrelated authorisation labels higher on page.
            footer_auth = [y for y in authorised_ys if y < y_for]
            if not footer_auth:
                raise ValueError("No Authorised Signatory anchor below the Pallia footer")
            y_auth = max(footer_auth)
            gap = y_for - y_auth
            logger.info(
                "Sig anchors — For Pallia y={:.1f}, Auth Signatory y={:.1f}, gap={:.1f} pts",
                y_for, y_auth, gap,
            )
            if gap >= 20:
                margin = 4.0
                x1 = page_width * 0.57
                x2 = page_width - 15.0
                box = (x1, y_auth + margin, x2, y_for - margin)
                logger.info("Auto-detected sig box: {}", tuple(round(v, 1) for v in box))
                return box
            logger.warning("Detected gap {:.1f} pts is too small; using fallback box.", gap)
    except Exception as exc:
        logger.warning("Sig-zone auto-detection failed ({}); using fallback box.", exc)

    logger.warning("Using fallback SIG_BOX {}.", _SIG_BOX_FALLBACK)
    return _SIG_BOX_FALLBACK


# ── CN extraction from certificate ───────────────────────────────────────────

def _extract_cn(cert) -> str:
    """Extract the Common Name from an asn1crypto x509 Certificate."""
    try:
        for rdn in cert.subject.chosen:
            for attr in rdn:
                if attr["type"].native == "common_name":
                    val = str(attr["value"].native).strip()
                    # Some DSC certs have format "VINOD (VINOD PALLIATRANS)" — keep first part
                    return val.split("(")[0].strip()
    except Exception:
        pass
    try:
        # Fallback: asn1crypto subject.human_friendly → "Common Name: VINOD, ..."
        hf = cert.subject.human_friendly
        for part in hf.split(","):
            part = part.strip()
            if part.lower().startswith("common name"):
                return part.split(":", 1)[-1].strip()
    except Exception:
        pass
    return "Authorised Signatory"


# ── Certificate discovery ─────────────────────────────────────────────────────

def list_token_certs(pin: str) -> list[str]:
    """Return all certificate labels present on the USB token.

    Useful for diagnosing a wrong/stale CERT_LABEL after a DSC renewal.
    """
    try:
        from pyhanko.sign.pkcs11 import open_pkcs11_session
    except ImportError as exc:
        raise SigningError("pyhanko not installed. Run: pip install pyhanko[pkcs11]") from exc

    try:
        session_ctx = open_pkcs11_session(
            lib_location=PKCS11_LIB,
            slot_no=SLOT_NO,
            user_pin=pin,
        )
    except Exception as exc:
        err = str(exc)
        if any(k in err for k in ("CKR_TOKEN_NOT_PRESENT", "CKR_SLOT_ID_INVALID",
                                   "No module", "CKR_GENERAL_ERROR")):
            raise TokenNotFound("USB token not found.") from exc
        if any(k in err for k in ("CKR_PIN_INCORRECT", "CKR_PIN_LOCKED")):
            raise WrongPIN("Incorrect PIN.") from exc
        raise SigningError(f"Could not open token: {exc}") from exc

    labels: list[str] = []
    try:
        with session_ctx as session:
            try:
                import pkcs11 as _p11
                for obj in session.get_objects({_p11.Attribute.CLASS: _p11.ObjectClass.CERTIFICATE}):
                    try:
                        label = obj[_p11.Attribute.LABEL]
                        labels.append(label if isinstance(label, str) else label.decode("utf-8", errors="replace"))
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("Could not enumerate token certs: {}", exc)
    except Exception:
        pass
    return labels


# ── Main signing function ─────────────────────────────────────────────────────

def sign_invoice_pdf(pdf_path: Path, pin: str) -> Path:
    """Sign *pdf_path* with the USB DSC token and return the signed PDF path.

    The signed file is saved as  <original_stem>_signed.pdf  in the same folder.
    Calling this a second time overwrites the previous signed copy.
    """
    if not pdf_path.exists():
        raise SigningError(f"PDF not found: {pdf_path}")

    signed_path = pdf_path.parent / f"{pdf_path.stem}_signed.pdf"

    try:
        from pyhanko.sign import signers
        from pyhanko.sign.fields import SigFieldSpec
        from pyhanko.sign.pkcs11 import PKCS11Signer, open_pkcs11_session
        from pyhanko.sign.signers.pdf_signer import PdfSigner
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.pdf_utils.images import PdfImage
        from pyhanko.stamp import StaticStampStyle
    except ImportError as exc:
        raise SigningError("pyhanko not installed. Run: pip install pyhanko[pkcs11]") from exc

    logger.info("Signing PDF {} with token slot={}", pdf_path.name, SLOT_NO)

    try:
        session_ctx = open_pkcs11_session(
            lib_location=PKCS11_LIB,
            slot_no=SLOT_NO,
            user_pin=pin,
        )
    except Exception as exc:
        err = str(exc)
        if any(k in err for k in ("CKR_TOKEN_NOT_PRESENT", "CKR_SLOT_ID_INVALID",
                                   "No module", "CKR_GENERAL_ERROR")):
            raise TokenNotFound("USB token not found. Please plug in the DSC pendrive.") from exc
        if any(k in err for k in ("CKR_PIN_INCORRECT", "CKR_PIN_LOCKED")):
            raise WrongPIN("Incorrect PIN. Please try again.") from exc
        raise SigningError(f"Could not open token session: {exc}") from exc

    try:
        with session_ctx as session:
            from signing_helper.token_certificate import select_signing_certificate

            try:
                cert, key_selector, chain = select_signing_certificate(session, CERT_LABEL)
            except ValueError as exc:
                raise SigningError(str(exc)) from exc
            cms_signer = PKCS11Signer(pkcs11_session=session, signing_cert=cert,
                                      ca_chain=chain, **key_selector)
            _ = cms_signer.signing_cert

            try:
                signer_name = _extract_cn(cms_signer.signing_cert)
            except Exception:
                signer_name = "Authorised Signatory"
            logger.info("DSC signer name: {}", signer_name)

            timestamp_str = datetime.now().strftime(_TS_FMT)

            appearance_img = _build_appearance_image(signer_name, timestamp_str)
            stamp_style = StaticStampStyle(
                background=PdfImage(appearance_img),
                border_width=0,
                background_opacity=1.0,
            )

            sig_box = _find_signature_box(pdf_path)

            pdf_signer_obj = PdfSigner(
                signature_meta=signers.PdfSignatureMetadata(
                    field_name="AuthorisedSignatory",
                ),
                signer=cms_signer,
                stamp_style=stamp_style,
                new_field_spec=SigFieldSpec(
                    sig_field_name="AuthorisedSignatory",
                    on_page=0,
                    box=sig_box,
                ),
            )

            with open(pdf_path, "rb") as f:
                writer = IncrementalPdfFileWriter(f)
                sig_result = pdf_signer_obj.sign_pdf(writer)
                signed_bytes = sig_result.getvalue()

    except (TokenNotFound, WrongPIN, SigningError):
        raise
    except Exception as exc:
        err = str(exc)
        if any(k in err for k in ("CKR_PIN_INCORRECT", "CKR_PIN_LOCKED")):
            raise WrongPIN("Incorrect PIN. Please try again.") from exc
        if "CKR_TOKEN_NOT_PRESENT" in err:
            raise TokenNotFound("USB token not found. Please plug in the DSC pendrive.") from exc
        raise SigningError(f"Signing failed: {exc}") from exc

    signed_path.write_bytes(signed_bytes)
    logger.info("Signed PDF saved: {}", signed_path)
    return signed_path
