from datetime import datetime, timedelta, timezone

import pkcs11
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from signing_helper.token_certificate import select_signing_certificate


@pytest.fixture(scope='module')
def certificate_factory():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def make(label, cert_id, *, ca=False, expired=False, signing=True):
        now = datetime.now(timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=10))
                .not_valid_after(now + timedelta(days=-1 if expired else 10))
                .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
                .add_extension(x509.KeyUsage(digital_signature=signing and not ca, content_commitment=False,
                    key_encipherment=not signing, data_encipherment=False, key_agreement=False,
                    key_cert_sign=ca, crl_sign=ca, encipher_only=False, decipher_only=False), critical=True)
                .sign(key, hashes.SHA256()))
        return {pkcs11.Attribute.CLASS: pkcs11.ObjectClass.CERTIFICATE,
                pkcs11.Attribute.LABEL: label, pkcs11.Attribute.ID: cert_id,
                pkcs11.Attribute.VALUE: cert.public_bytes(serialization.Encoding.DER)}

    return make


def private_key(cert_id, label='private-key'):
    return {pkcs11.Attribute.CLASS: pkcs11.ObjectClass.PRIVATE_KEY,
            pkcs11.Attribute.SIGN: True, pkcs11.Attribute.ID: cert_id,
            pkcs11.Attribute.LABEL: label}


class Session:
    def __init__(self, *objects):
        self.objects = objects

    def get_objects(self, query):
        return (obj for obj in self.objects if all(obj.get(key) == value for key, value in query.items()))


def test_selects_renewed_signer_and_matching_key_instead_of_issuer_certificates(certificate_factory):
    make = certificate_factory
    session = Session(make('renewed-signer', b'leaf'), make('root', b'root', ca=True),
                      make('issuer', b'issuer', ca=True), private_key(b'leaf'))
    cert, selector, chain = select_signing_certificate(session, 'old-missing-label')
    assert cert.subject.native['common_name'] == 'renewed-signer'
    assert selector == {'key_id': b'leaf'}
    assert len(chain) == 2 and all(cert.ca for cert in chain)


def test_excludes_expired_encryption_and_unmatched_certificates(certificate_factory):
    make = certificate_factory
    session = Session(make('expired', b'expired', expired=True), private_key(b'expired'),
                      make('encryption', b'encryption', signing=False), private_key(b'encryption'),
                      make('unmatched', b'missing'), make('current', b'current'), private_key(b'current'))
    cert, selector, _ = select_signing_certificate(session, 'expired')
    assert cert.subject.native['common_name'] == 'current'
    assert selector == {'key_id': b'current'}


def test_does_not_guess_between_two_usable_signing_identities(certificate_factory):
    make = certificate_factory
    session = Session(make('one', b'one'), private_key(b'one'), make('two', b'two'), private_key(b'two'))
    with pytest.raises(ValueError, match='Multiple current signing certificates'):
        select_signing_certificate(session, 'missing')
    cert, selector, _ = select_signing_certificate(session, 'two')
    assert cert.subject.native['common_name'] == 'two'
    assert selector == {'key_id': b'two'}


def test_rejects_certificates_without_a_signing_key(certificate_factory):
    with pytest.raises(ValueError, match='No current signing certificate'):
        select_signing_certificate(Session(certificate_factory('leaf', b'leaf')))


def test_label_matching_is_available_for_tokens_without_certificate_ids(certificate_factory):
    session = Session(certificate_factory('signer', b''), private_key(b'', label='signer'))
    _, selector, _ = select_signing_certificate(session)
    assert selector == {'key_label': 'signer'}
