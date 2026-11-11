"""Read versions, loaded mappings and ordinary TLS ciphers without networking."""
import hashlib
import json
from pathlib import Path
import ssl
import _ssl

import psycopg
from cryptography.hazmat.backends.openssl.backend import backend


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


paths = sorted({line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines()
                if any(x in line for x in ('libssl', 'libcrypto', '_ssl.', 'cryptography/hazmat/bindings/_rust'))})
context = ssl.create_default_context()
print(json.dumps({'system_python_openssl': ssl.OPENSSL_VERSION,
                  'system_python_ssl_module': {'path': _ssl.__file__, 'sha256': sha(_ssl.__file__)},
                  'cryptography_embedded_openssl': backend.openssl_version_text(),
                  'psycopg_impl': psycopg.pq.__impl__, 'libpq_version': psycopg.pq.version(),
                  'tls_cipher_names': [r['name'] for r in context.get_ciphers()],
                  'ssl_module_quic_attributes': [n for n in dir(ssl) if 'QUIC' in n.upper()],
                  'native_ssl_module_quic_attributes': [n for n in dir(_ssl) if 'QUIC' in n.upper()],
                  'loaded': [{'path': p, 'sha256': sha(p)} for p in paths],
                  'network_calls': 0, 'provider_calls': 0}, indent=2))
