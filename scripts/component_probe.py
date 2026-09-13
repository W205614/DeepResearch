"""Internal isolated-test probe; never prints tokens or response bodies on errors."""
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from backend.core.config import Settings
from backend.core.auth import issue_development_token
from backend.core.db import uid
from backend.core.postgres import PostgresDatabase


async def main():
    settings = Settings()
    headers = {'Authorization': 'Bearer ' + issue_development_token(settings, 'drill-user')}
    async with httpx.AsyncClient(base_url='http://api:8000', headers=headers, timeout=60) as client:
        action = sys.argv[1]
        if action == 'create':
            response = await client.post('/api/research/runs', json={'data_policy':'public','topic': sys.argv[2], 'client_request_id': uid()})
            print(json.dumps({'http': response.status_code, 'id': response.json().get('id')}))
        elif action == 'ready':
            print(json.dumps({'http': (await client.get('/readyz')).status_code}))
        elif action == 'capabilities':
            response = await client.get('/api/capabilities')
            print(json.dumps(response.json()))
        else:
            db = PostgresDatabase(settings.database_url)
            await db.init()
            try:
                row = await db.one('SELECT status,attempt_count FROM runs WHERE id=?', (sys.argv[2],))
                row['done_events'] = (await db.one("SELECT COUNT(*) AS total FROM events WHERE run_id=? AND type='done'", (sys.argv[2],)))['total']
                row['paused'] = bool(await db.one('SELECT key FROM metadata WHERE key=?', ('slow:' + sys.argv[2],)))
                print(json.dumps(row))
            finally:
                await db.close()


if __name__ == '__main__':
    asyncio.run(main())
