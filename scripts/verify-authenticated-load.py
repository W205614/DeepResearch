"""Create disposable OIDC credentials, run k6, and remove only fixture identities."""
import os
from pathlib import Path
import subprocess
import uuid

import httpx
from dotenv import dotenv_values


def main():
    root = Path(__file__).resolve().parents[1]
    config = {**dotenv_values(root / '.env'), **os.environ}
    name = 'load-' + uuid.uuid4().hex
    password = uuid.uuid4().hex
    realm = 'http://localhost:8180'
    client_id = user_id = None
    with httpx.Client(base_url=realm, timeout=30, trust_env=False) as client:
        response = client.post('/realms/master/protocol/openid-connect/token', data={
            'client_id': 'admin-cli', 'grant_type': 'password', 'username': 'admin',
            'password': config['KEYCLOAK_ADMIN_PASSWORD']})
        response.raise_for_status()
        headers = {'Authorization': 'Bearer ' + response.json()['access_token']}
        admin = '/admin/realms/deepresearch'
        try:
            response = client.post(admin + '/clients', headers=headers, json={
                'clientId': name, 'publicClient': True, 'directAccessGrantsEnabled': True,
                'standardFlowEnabled': False, 'attributes': {'access.token.lifespan': '900'},
                'protocolMappers': [{'name': 'audience', 'protocol': 'openid-connect',
                    'protocolMapper': 'oidc-audience-mapper', 'config': {
                        'included.client.audience': 'deepresearch-api', 'access.token.claim': 'true'}}]})
            response.raise_for_status()
            client_id = response.headers['location'].rsplit('/', 1)[1]
            response = client.post(admin + '/users', headers=headers, json={
                'username': name, 'enabled': True, 'emailVerified': True,
                'email': name + '@example.invalid', 'firstName': 'Load', 'lastName': 'Fixture',
                'credentials': [{'type': 'password', 'value': password, 'temporary': False}]})
            response.raise_for_status()
            user_id = response.headers['location'].rsplit('/', 1)[1]
            uuid.UUID(user_id)
            response = client.post('/realms/deepresearch/protocol/openid-connect/token', data={
                'client_id': name, 'grant_type': 'password', 'username': name, 'password': password})
            response.raise_for_status()
            token = response.json()['access_token']
            probe = client.get('http://localhost:8080/api/metrics', headers={'Authorization': 'Bearer ' + token})
            probe.raise_for_status()
            result = subprocess.run(['docker', 'run', '--rm', '-i', '-e', 'AUTHORIZATION',
                'grafana/k6:0.54.0', 'run', '-'], input=(root / 'scripts/load-authenticated-read.js').read_bytes(),
                env={**os.environ, 'AUTHORIZATION': 'Bearer ' + token}, capture_output=True)
            output = result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')
            output = output.replace(token, '[redacted]')
            target = root / '.cache/verification/authenticated-load.txt'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(output, encoding='utf-8')
            print(output[-5000:])
            if result.returncode:
                raise RuntimeError('Authenticated load thresholds failed')
        finally:
            # The fixture owns this automatically created personal workspace.
            if user_id:
                sql = ';'.join(f"DELETE FROM {table} WHERE {field}='{user_id}'" for table, field in [
                    ('audit_logs', 'workspace_id'), ('workspace_limits', 'workspace_id'),
                    ('memberships', 'workspace_id'), ('workspaces', 'id')])
                subprocess.run(['docker', 'compose', 'exec', '-T', 'postgres', 'psql', '-v',
                    'ON_ERROR_STOP=1', '-U', 'deepresearch', '-d', 'deepresearch', '-c', sql],
                    check=True, capture_output=True)
                client.delete(admin + '/users/' + user_id, headers=headers).raise_for_status()
            if client_id:
                client.delete(admin + '/clients/' + client_id, headers=headers).raise_for_status()


if __name__ == '__main__':
    main()
