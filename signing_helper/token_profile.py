"""Restrict the three signing flows to their intended certificate identities."""
from datetime import datetime, timezone

SIGNERS = {"eee": "JISHNU NANDA", "pallia-billing": "VINOD",
           "pallia-accounts": "RAMASHANKAR SHARMA"}


def normalized_name(name):
    return " ".join(name.split("(", 1)[0].split()).upper()


def certificate_name(cert):
    return str(cert.subject.native.get("common_name", "")).strip()


def public_signer_names(token):
    import pkcs11
    from asn1crypto import x509

    names = []
    now = datetime.now(timezone.utc)
    with token.open() as session:
        for obj in session.get_objects({pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
            cert = x509.Certificate.load(obj[pkcs11.Attribute.VALUE])
            validity = cert["tbs_certificate"]["validity"].native
            usage = cert.key_usage_value
            if cert.ca or not validity["not_before"] <= now <= validity["not_after"]:
                continue
            if usage is not None and not {"digital_signature", "non_repudiation"}.intersection(usage.native):
                continue
            name = certificate_name(cert)
            if name and name not in names:
                names.append(name)
    return names


def validate_names(names, profile):
    expected = SIGNERS.get(profile)
    if expected is None:
        raise ValueError("Unknown DSC signing profile.")
    if not any(normalized_name(name) == expected for name in names):
        found = ", ".join(names) or "no readable current signing certificate"
        raise ValueError(f"This signing flow requires {expected}. Connected DSC: {found}. Select the correct token; no PIN was attempted on this device.")


def validate_token_profile(token, profile):
    validate_names(public_signer_names(token), profile)


def validate_certificate_profile(cert, profile):
    expected = SIGNERS.get(profile)
    if expected is None or normalized_name(certificate_name(cert)) != expected:
        raise ValueError(f"Signing stopped: this flow requires {expected or 'a valid signing profile'}, but the selected certificate is {certificate_name(cert)}.")


def describe_token(token, profile=None):
    names = public_signer_names(token)
    allowed = [key for key, expected in SIGNERS.items()
               if any(normalized_name(name) == expected for name in names)]
    return {"signer_name": ", ".join(names) or "Unknown signer", "profiles": allowed,
            "eligible": profile is None or profile in allowed}
