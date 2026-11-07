"""Real PG type inference; temporary tables never mutate application evidence."""
import os

import asyncpg
import pytest

from backend.core.config import Settings
from backend.core.postgres import PostgresDatabase
from scripts.check_postgres_lexical import verify

pytestmark = pytest.mark.skipif(os.getenv('INTEGRATION') != '1', reason='Requires isolated real PostgreSQL')


async def test_weighted_values_cte_binds_numeric_weights_with_real_postgres():
    database = PostgresDatabase(os.getenv('LEXICAL_PG_TEST_DSN') or Settings().database_url)
    database.pool = await asyncpg.create_pool(database.dsn, min_size=1, max_size=1)
    try:
        async with database.pool.acquire() as connection:
            await connection.execute('''
                CREATE TEMP TABLE documents(id TEXT,user_id TEXT,status TEXT);
                CREATE TEMP TABLE chunks(id TEXT,document_id TEXT,user_id TEXT);
                CREATE TEMP TABLE lexical_chunks(chunk_id TEXT,workspace_id TEXT,document_id TEXT,content_hash TEXT,length INTEGER);
                CREATE TEMP TABLE chunk_terms(workspace_id TEXT,term TEXT,chunk_id TEXT,frequency INTEGER);
                CREATE TEMP TABLE memberships(workspace_id TEXT,subject TEXT,role TEXT);
                CREATE TEMP TABLE document_policies(document_id TEXT,workspace_id TEXT,collection_id TEXT,owner_subject TEXT);
                CREATE TEMP TABLE collections(id TEXT,workspace_id TEXT,visibility TEXT);
                CREATE TEMP TABLE collection_grants(collection_id TEXT,subject TEXT,permission TEXT);
                INSERT INTO documents VALUES('d1','space','ready'),('d2','space','ready');
                INSERT INTO chunks VALUES('c1','d1','space'),('c2','d2','space');
                INSERT INTO lexical_chunks VALUES('c1','space','d1','hash1',4),('c2','space','d2','hash2',4);
                INSERT INTO chunk_terms VALUES('space','alpha','c1',3),('space','alpha','c2',1);
                INSERT INTO memberships VALUES('space','alice','admin');
            ''')
        result = await verify(database, 'space', 'alice', 'alpha')
        assert result['passed'] and result['candidates'] == 2
    finally:
        await database.close()
