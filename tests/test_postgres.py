import pytest

from backend.core.config import Settings
from backend.core.db import Database
from backend.core.postgres import PostgresDatabase
from backend.services.runtime import Runtime


async def test_non_demo_sqlite_requires_explicit_isolated_mode(tmp_path):
    settings = Settings(_env_file=None, demo_mode=False, database_url="", data_dir=tmp_path)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Runtime(settings)
    runtime = Runtime(settings, isolated_mode=True)
    try:
        assert isinstance(runtime.db, Database)
    finally:
        await runtime.providers.close()


def test_postgres_rewrites_sqlite_upserts_before_binding_parameters():
    db = PostgresDatabase("postgresql://unused")

    assert db._sql("INSERT OR REPLACE INTO search_cache(run_id,query,result) VALUES(?,?,?)") == (
        "INSERT INTO search_cache(run_id,query,result) VALUES($1,$2,$3) "
        "ON CONFLICT(run_id,query) DO UPDATE SET result=EXCLUDED.result"
    )
    assert db._sql("INSERT OR REPLACE INTO web_cache(url,text,fetched_at,access,error) VALUES(?,?,?,?,?)") == (
        "INSERT INTO web_cache(url,text,fetched_at,access,error) VALUES($1,$2,$3,$4,$5) "
        "ON CONFLICT(url) DO UPDATE SET text=EXCLUDED.text,fetched_at=EXCLUDED.fetched_at,"
        "access=EXCLUDED.access,error=EXCLUDED.error"
    )
    assert db._sql("""INSERT OR REPLACE INTO document_search_cache
        (user_id,corpus_hash,query_hash,limit_value,result,created_at) VALUES(?,?,?,?,?,?)""") == (
        "INSERT INTO document_search_cache(user_id,corpus_hash,query_hash,limit_value,result,created_at) "
        "VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(user_id,corpus_hash,query_hash,limit_value) "
        "DO UPDATE SET result=EXCLUDED.result,created_at=EXCLUDED.created_at"
    )
    assert db._sql("INSERT OR IGNORE INTO counters(run_id) VALUES(?)") == (
        "INSERT INTO counters(run_id) VALUES($1) ON CONFLICT DO NOTHING"
    )
