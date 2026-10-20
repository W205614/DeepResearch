"""Prepare local-only OIDC/CSP fixtures without changing production settings."""
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / '.cache/lifecycle-acceptance'
    target.mkdir(parents=True, exist_ok=True)
    realm = json.loads((root / 'keycloak/deepresearch-realm.json').read_text(encoding='utf-8'))
    client = next(c for c in realm['clients'] if c['clientId'] == 'deepresearch-web')
    client['redirectUris'] = ['http://127.0.0.1:28080/*', 'http://localhost:28080/*']
    client['webOrigins'] = ['http://127.0.0.1:28080', 'http://localhost:28080']
    (target / 'realm.json').write_text(json.dumps(realm), encoding='utf-8')
    nginx = (root / 'frontend/nginx.conf').read_text(encoding='utf-8')
    (target / 'nginx.conf').write_text(nginx.replace('http://localhost:8180', 'http://localhost:18480'), encoding='utf-8')
    print('Prepared isolated localhost:28080 / OIDC localhost:18480 fixtures.')


if __name__ == '__main__':
    main()
