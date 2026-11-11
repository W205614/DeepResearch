"""Exercise real installed API rejection contracts; no network or CMS encryption."""
from datetime import datetime, timedelta, timezone
import importlib.metadata
import json
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV
from cryptography.hazmat.primitives.serialization.pkcs7 import PKCS7EnvelopeBuilder
from cryptography.x509.oid import NameOID

key = AESGCMSIV.generate_key(bit_length=128)
cipher = AESGCMSIV(key)
rejections = []
for length in (0, 11, 13, 16):
    try:
        cipher.encrypt(b'0' * length, b'synthetic-data', b'complete-aad')
    except ValueError as error:
        rejections.append({'nonce_bytes': length, 'rejected': True, 'message': str(error)})
    else:
        raise AssertionError('Nonstandard nonce length was accepted')
nonce = b'fixed-test12'
assert len(nonce) == 12
encrypted = cipher.encrypt(nonce, b'synthetic-data', b'complete-aad')
assert cipher.decrypt(nonce, encrypted, b'complete-aad') == b'synthetic-data'
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'synthetic-probe.invalid')])
private_key = ec.generate_private_key(ec.SECP256R1())
now = datetime.now(timezone.utc)
certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
               .public_key(private_key.public_key()).serial_number(1)
               .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(minutes=1))
               .sign(private_key, hashes.SHA256()))
try:
    PKCS7EnvelopeBuilder().set_data(b'synthetic-data').add_recipient(certificate)
except TypeError as error:
    rejected = {'rejected': True, 'message': str(error)}
else:
    raise AssertionError('EC recipient was accepted')
print(json.dumps({'python_version': sys.version, 'cryptography_version': importlib.metadata.version('cryptography'),
                  'aes_gcm_siv_nonce_rejections': rejections, 'aes_gcm_siv_12_byte_roundtrip': True,
                  'aes_gcm_siv_incremental_update_methods': [n for n in dir(cipher) if 'update' in n.lower()],
                  'pkcs7_ec_recipient_rejected_before_encryption': rejected,
                  'network_calls': 0, 'provider_calls': 0}, indent=2))
