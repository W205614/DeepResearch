"""Persist loopback OIDC callback settings after a local port change.

Requires the running Compose project to publish exactly the requested endpoints.
Secrets remain in .env / process memory; never print request bodies or tokens.
"""
import argparse
import json
import subprocess
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values


def main(args):
    values = dotenv_values('.env')
    origin = args.web.rstrip('/')
    identity = args.identity.rstrip('/')
    for service, internal, url in [('web', '80', origin), ('keycloak', '8080', identity)]:
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1'} or parsed.username:
            raise ValueError('Only loopback HTTP endpoints are accepted')
        result = subprocess.run(['docker', 'compose', 'port', service, internal], capture_output=True, text=True, check=True)
        if result.stdout.strip().rsplit(':', 1)[-1] != str(parsed.port or 80):
            raise ValueError('Endpoint does not match the selected Compose project')
    with httpx.Client(timeout=15, trust_env=False) as client:
        token = client.post(identity + '/realms/master/protocol/openid-connect/token', data={
            'client_id': 'admin-cli', 'grant_type': 'password', 'username': 'admin',
            'password': values.get('KEYCLOAK_ADMIN_PASSWORD', '')})
        if token.status_code != 200:
            raise RuntimeError('OIDC administration login failed; response withheld')
        headers = {'Authorization': 'Bearer ' + token.json()['access_token']}
        result = client.get(identity + '/admin/realms/deepresearch/clients', params={'clientId': 'deepresearch-web'}, headers=headers)
        result.raise_for_status()
        clients = result.json()
        if len(clients) != 1:
            raise RuntimeError('OIDC client is not unique')
        entry = clients[0]
        port = urlsplit(origin).port or 80
        origins = [f'http://localhost:{port}', f'http://127.0.0.1:{port}']
        entry.update(redirectUris=[value + '/*' for value in origins], webOrigins=origins)
        result = client.put(identity + '/admin/realms/deepresearch/clients/' + entry['id'], headers=headers, json=entry)
        result.raise_for_status()
        print(json.dumps({'configured': True, 'web_origins': origins, 'identity': identity}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--web', default='http://localhost:8080')
    parser.add_argument('--identity', default='http://localhost:8480')
    main(parser.parse_args())
