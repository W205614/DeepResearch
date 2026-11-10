from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import ssl
from threading import Thread
import time
from unittest.mock import AsyncMock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
import jwt
from pydantic import ValidationError
import pytest

from backend.core.auth import TokenVerifier
from backend.core.config import Settings


@pytest.fixture
def identity_https(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Isolated identity test CA')])
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .sign(key, hashes.SHA256()))
    ca, private = tmp_path / 'ca.pem', tmp_path / 'private.pem'
    ca.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"keys": []}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(ca), str(private))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ca, 'https://127.0.0.1:' + str(server.server_port) + '/jwks'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def test_oidc_explicit_private_ca_verifies_real_https_and_env_cannot_bypass_it(settings, identity_https, monkeypatch):
    ca, endpoint = identity_https
    settings.oidc_jwks_url = endpoint
    monkeypatch.setenv('SSL_CERT_FILE', str(ca))
    with pytest.raises(HTTPException) as failure:
        await TokenVerifier(settings)._key_set()
    assert failure.value.status_code == 503
    assert str(ca) not in failure.value.detail and endpoint not in failure.value.detail
    settings.oidc_ca_certs = ca
    verifier = TokenVerifier(settings)
    assert isinstance(verifier._verify, ssl.SSLContext)
    assert await verifier._key_set() == {'keys': []}


def test_oidc_ca_config_accepts_empty_and_rejects_missing_file(tmp_path):
    assert Settings(_env_file=None, oidc_ca_certs='').oidc_ca_certs is None
    missing = str(tmp_path / 'private-ca.pem')
    with pytest.raises(ValidationError) as failure:
        Settings(_env_file=None, oidc_ca_certs=missing)
    assert missing not in str(failure.value)


async def test_oidc_rejects_missing_iat_and_retains_verified_issued_at(settings):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {'kid': 'test'}
    settings.auth_mode, settings.oidc_issuer = 'oidc', 'https://identity.example.invalid'
    verifier = TokenVerifier(settings)
    verifier._key_set = AsyncMock(return_value={'keys': [public]})
    claims = {'sub': 'alice', 'aud': settings.oidc_audience, 'iss': settings.oidc_issuer, 'exp': int(time.time()) + 120}
    def token(value):
        return 'Bearer ' + jwt.encode(value, key, algorithm='RS256', headers={'kid': 'test'})
    with pytest.raises(HTTPException) as failure:
        await verifier.verify(token(claims))
    assert failure.value.status_code == 401
    stamp = int(time.time())
    principal = await verifier.verify(token(claims | {'iat': stamp}))
    assert principal.subject == 'alice' and principal.issued_at == stamp
