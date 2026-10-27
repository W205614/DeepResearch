"""No-network version/CA regression gate for the actual CI paid interpreter."""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.verify_python_runtime import handshake  # noqa:E402

VERSION = (3, 11, 18)
SOURCE_SHA = '9120e0a653868f6e3698d36cdd99e5a58903ec9469e448f21d98f7b16d8fa290'
FIXTURE_SHA = '8a9874e20b8583e80e9d7d6c82ea566c22d50458a93bfef6400d70dde132a053'
IDENTITY = Path('/usr/local/share/deepresearch-python-runtime.json')


def runtime_identity(path=IDENTITY):
    if sys.version_info[:3] != VERSION or os.getuid() == 0:
        raise ValueError('Paid CI requires actual non-root CPython3.11.18')
    value = json.loads(path.read_bytes())
    if (value['python_version'] != '3.11.18' or value['expat'] != 'expat_2.9.0'
            or value['signed_source']['source_sha256'] != SOURCE_SHA):
        raise ValueError('Interpreter signed-source identity differs')
    return value


def valid_tls(root):
    # Ephemeral local credentials never enter source control or artifacts.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    now = datetime.now(timezone.utc)
    ca_key, leaf_key = (ec.generate_private_key(ec.SECP256R1()) for _ in range(2))
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Ephemeral CI fixture CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
          .not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')]))
            .issuer_name(ca_name).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False).sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    cert, key = root/'valid.pem', root/'valid.key'
    cert.write_bytes(leaf.public_bytes(pem)+ca.public_bytes(pem))
    key.write_bytes(leaf_key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.load_verify_locations(cadata=ca.public_bytes(pem).decode())
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert, key)
    return handshake(client, server)


def verify(root=ROOT):
    identity = runtime_identity()
    data = (root/'eval/python-runtime/distrusted.zip').read_bytes()
    if hashlib.sha256(data).hexdigest() != FIXTURE_SHA:
        raise ValueError('Official CA regression fixture bytes differ')
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            archive.extractall(directory, pwd=b'Brave Sir Robin')  # Upstream public test password.
        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.load_verify_locations(directory/'distrusted/distrusted_root.pem')
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(directory/'distrusted/distrusted_keycert.pem')
        try:
            handshake(client, server)
            raise ValueError('Compromised CA accepted by paid CI interpreter')
        except ssl.SSLCertVerificationError as error:
            if error.verify_code != 28:
                raise ValueError('CA regression failed for a different reason') from None
        tls = valid_tls(directory)
    manifest = json.loads(Path('/app/source-fingerprint.json').read_bytes())
    return {'schema': 'ci-python-preflight-v1', 'passed': True, 'python_version': '3.11.18',
            'source_sha256': SOURCE_SHA, 'compromised_ca_verify_code': 28, 'valid_tls': tls,
            'uid': os.getuid(), 'gid': os.getgid(), 'fixture_sha256': FIXTURE_SHA,
            'image_source_files': manifest, 'runtime_identity': identity,
            'supplier_http': 0, 'network_http': 0}


if __name__ == '__main__':
    print(json.dumps(verify(), sort_keys=True))
