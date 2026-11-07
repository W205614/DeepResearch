"""Provision identities ONLY inside the explicitly isolated acceptance realm."""
import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from scripts.load_system_performance import KEYCLOAK, provision_tokens, wait_for_keycloak


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated', action='store_true', required=True)
    parser.add_argument('--accounts', type=int, default=50)
    parser.add_argument('--output', type=Path, default=Path('/receipts/tokens.json'))
    args = parser.parse_args()
    if os.getenv('INTEGRATION') != '1' or KEYCLOAK != 'http://keycloak:8080':
        raise SystemExit('Only the private dr-production-test Keycloak fixture is supported')
    if not 10 <= args.accounts <= 50:
        raise SystemExit('Fixture account count must be between 10 and 50')
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        token = await wait_for_keycloak(client)
        response = await client.put(KEYCLOAK + '/admin/realms/deepresearch-perf',
            headers={'Authorization': 'Bearer ' + token},
            json={'accessTokenLifespan': 7200, 'sslRequired': 'none'})
        response.raise_for_status()
        tokens = await provision_tokens(client, args.accounts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(tokens), encoding='utf-8')
    args.output.chmod(0o600)
    print(json.dumps({'accounts': len(tokens), 'isolated_realm': 'deepresearch-perf', 'tokens_printed': False}))


if __name__ == '__main__':
    asyncio.run(main())
