"""Resolve a current signing certificate and its matching token private key."""
from datetime import datetime, timezone


def select_signing_certificate(session, preferred_label=None):
    import pkcs11
    from asn1crypto import x509

    attr = pkcs11.Attribute
    keys = list(session.get_objects({attr.CLASS: pkcs11.ObjectClass.PRIVATE_KEY, attr.SIGN: True}))
    certificates = []
    for obj in session.get_objects({attr.CLASS: pkcs11.ObjectClass.CERTIFICATE}):
        certificates.append((obj, x509.Certificate.load(obj[attr.VALUE])))

    now = datetime.now(timezone.utc)
    candidates = []
    chain = [cert for _, cert in certificates if cert.ca]
    for obj, cert in certificates:
        if cert.ca:
            continue
        validity = cert['tbs_certificate']['validity'].native
        if not validity['not_before'] <= now <= validity['not_after']:
            continue
        usage = cert.key_usage_value
        if usage is not None and not {'digital_signature', 'non_repudiation'}.intersection(usage.native):
            continue
        cert_id = obj[attr.ID]
        label = obj[attr.LABEL]
        if cert_id:
            matching_keys = [key for key in keys if key[attr.ID] == cert_id]
            selector = {'key_id': cert_id}
        else:
            matching_keys = [key for key in keys if label and key[attr.LABEL] == label]
            selector = {'key_label': label}
        if len(matching_keys) == 1:
            candidates.append((label, cert, selector))

    preferred = [candidate for candidate in candidates if candidate[0] == preferred_label]
    if len(preferred) == 1:
        candidates = preferred
    if not candidates:
        raise ValueError('No current signing certificate with a matching private key was found on the DSC token.')
    if len(candidates) != 1:
        raise ValueError('Multiple current signing certificates have matching private keys. Select the intended DSC certificate before signing.')
    _, cert, selector = candidates[0]
    return cert, selector, chain
