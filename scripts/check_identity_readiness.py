"""Read-only Keycloak recovery/MFA/transport readiness; never sends mail or enrolls users."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def inspect(base, realm):
    parts = urlsplit(base)
    if parts.scheme != 'https' and not (parts.scheme == 'http' and parts.hostname in {'localhost', '127.0.0.1', 'keycloak'}):
        raise ValueError('Use HTTPS or an explicitly local identity endpoint')
    if not realm or '/' in realm or realm in {'.', '..'}:
        raise ValueError('Explicit target realm required')
    with httpx.Client(base_url=base.rstrip('/'), timeout=10, trust_env=False) as client:
        token_response = client.post('/realms/master/protocol/openid-connect/token', data={
            'client_id': 'admin-cli', 'grant_type': 'password',
            'username': os.environ.get('KEYCLOAK_ADMIN_USERNAME', 'admin'),
            'password': os.environ['KEYCLOAK_ADMIN_PASSWORD']})
        token_response.raise_for_status()
        headers = {'Authorization': 'Bearer ' + token_response.json()['access_token']}
        response = client.get('/admin/realms/' + realm, headers=headers)
        response.raise_for_status()
        data = response.json()
        response = client.get('/admin/realms/' + realm + '/authentication/required-actions', headers=headers)
        response.raise_for_status()
        actions = response.json()
        otp = next((action for action in actions if action.get('alias') == 'CONFIGURE_TOTP'), {})
        return {'scope': 'read-only realm configuration; no email or enrollment performed',
                'password_reset_enabled': bool(data.get('resetPasswordAllowed')),
                'smtp_host_configured': bool(data.get('smtpServer', {}).get('host')),
                'smtp_delivery_tested': False, 'account_recovery_tested': False,
                'totp_supported': bool(otp.get('enabled')),
                'totp_new_user_enrollment_default': bool(otp.get('defaultAction')),
                'existing_user_mfa_enrollment_tested': False,
                'brute_force_protected': bool(data.get('bruteForceProtected')),
                'permanent_lockout': bool(data.get('permanentLockout')),
                'https_endpoint': parts.scheme == 'https',
                'external_hostname_tls_verified': False,
                'blocked_external': ['real SMTP delivery/account recovery', 'target-user MFA enrollment and recovery',
                                     'externally reachable TLS and hostname verification']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--realm', default='deepresearch')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        result = inspect(args.base_url, args.realm)
    except Exception:
        raise SystemExit('Identity readiness inspection failed; credentials and response withheld') from None
    Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
