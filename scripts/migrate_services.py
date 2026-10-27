"""Run schema DDL with the migrator, then apply runtime least-privilege grants."""
import asyncio
import os
import subprocess
import sys

import asyncpg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


JAVA_WRITE = {"workspaces", "memberships", "workspace_limits", "threads", "runs", "events",
              "audit_logs", "business_outbox", "report_publications", "report_series",
              "report_revision_jobs", "attachments", "memories", "workspace_corpus_revisions", "report_impacts",
              "collections", "collection_grants", "document_policies", "research_intakes"}
CONTROL_OWNED = {"workspaces", "memberships", "workspace_limits", "business_outbox", "report_series",
                 "collections", "collection_grants", "document_policies", "research_intakes"}
PYTHON_WRITE = {
    "runs", "threads", "events", "documents", "chunks", "memories", "counters", "search_cache", "web_cache",
    "document_search_cache", "metadata", "workspace_daily_usage", "document_cleanup", "dead_letter_runs",
    "provider_health", "call_attempts", "verified_claims", "attachments", "run_vision", "document_versions",
    "document_change_events", "workspace_corpus_revisions", "run_artifacts", "report_impacts", "report_revision_jobs",
    "document_scan_jobs", "run_cleanup", "retention_archives", "usage_periods", "usage_attempts", "provider_probes",
    "source_rechecks", "lexical_chunks", "chunk_terms", "checkpoints", "checkpoint_blobs", "checkpoint_writes",
}


async def grant_runtime(conn):
    tables = {row["tablename"] for row in await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    await conn.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM dr_java,dr_python,dr_monitor")
    await conn.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM dr_java,dr_python,dr_monitor")
    await conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO dr_java,dr_python")
    for table in sorted(tables):
        quoted = '"' + table.replace('"', '""') + '"'
        columns = [row['column_name'] for row in await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=$1", table)]
        if columns:
            names = ','.join('"' + value.replace('"', '""') + '"' for value in columns)
            # Table-level REVOKE does not remove explicit column grants from a
            # previous contract. Reset those too before applying today's list.
            await conn.execute(f"REVOKE ALL({names}) ON {quoted} FROM dr_java,dr_python,dr_monitor")
        if table in JAVA_WRITE:
            await conn.execute(f"GRANT INSERT,UPDATE,DELETE ON {quoted} TO dr_java")
        if table in PYTHON_WRITE:
            await conn.execute(f"GRANT INSERT,UPDATE,DELETE ON {quoted} TO dr_python")
    if "report_publications" in tables:
        # Evidence lifecycle can flag freshness, never author/approve/rewrite a snapshot.
        await conn.execute("GRANT UPDATE(evidence_status) ON report_publications TO dr_python")
    if "audit_logs" in tables:
        await conn.execute("GRANT INSERT ON audit_logs TO dr_java,dr_python")
        await conn.execute("REVOKE UPDATE,DELETE ON audit_logs FROM dr_java,dr_python")
    if "document_policies" in tables:
        await conn.execute("GRANT INSERT ON document_policies TO dr_python")
    if "runs" in tables:
        # Shared rows do not imply shared ownership of every field.
        await conn.execute("REVOKE UPDATE ON runs FROM dr_java,dr_python")
        await conn.execute("REVOKE INSERT ON runs FROM dr_python")
        await conn.execute("GRANT UPDATE(status,updated_at,report_submitted) ON runs TO dr_java")
        await conn.execute("GRANT UPDATE(status,updated_at,report,sources,validation,error,error_info,attempt_count,"
                           "last_attempt_at,deadline_at,call_attempts,reserved_tokens,auto_recoveries) ON runs TO dr_python")
    if "threads" in tables:
        await conn.execute("REVOKE INSERT,UPDATE ON threads FROM dr_python")
    if "report_revision_jobs" in tables:
        await conn.execute("REVOKE INSERT,UPDATE,DELETE ON report_revision_jobs FROM dr_python")
        await conn.execute("GRANT UPDATE(plan_json,updated_at) ON report_revision_jobs TO dr_python")
    if "events" in tables:
        await conn.execute("REVOKE UPDATE ON events FROM dr_java,dr_python")
        await conn.execute("REVOKE DELETE ON events FROM dr_java")
    if "events" in tables:
        await conn.execute("GRANT USAGE,SELECT ON SEQUENCE events_id_seq TO dr_java,dr_python")
    if "runs" in tables:
        await conn.execute("GRANT SELECT(status,created_at,last_attempt_at) ON runs TO dr_monitor")
    if "documents" in tables:
        await conn.execute("GRANT SELECT(status,updated_at) ON documents TO dr_monitor")
    # New schema objects receive no implicit runtime DDL/write grants. This
    # function reruns after every migration and is the central grant contract.
    await conn.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC")
    await conn.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC")


async def prepare(dsn):
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            await grant_runtime(conn)
    finally:
        await conn.close()


def main():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # This process is a short-lived deployment job, never a request handler.
    if subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=False).returncode:
        raise SystemExit("Alembic migration failed")
    # Populate the bounded SQL-derived lexical index before accepting requests;
    # a partial backfill is resumable but must not be reported as ready.
    backfill_env = {**os.environ, "DATABASE_URL": os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)}
    backfill = subprocess.run([sys.executable, "scripts/rebuild_lexical_index.py", "--all-workspaces", "--max-batches", "10000",
                               "--output", "/data/lexical-backfill.json"], env=backfill_env, capture_output=True, timeout=900)
    if backfill.returncode:
        raise SystemExit("Lexical backfill incomplete/failed; retained progress must be resumed before readiness")
    try:
        asyncio.run(prepare(os.environ["DATABASE_URL"]))
    except Exception:
        raise SystemExit("Checkpoint/grant preparation failed; private driver output withheld") from None
    print("Schema/checkpoints prepared; runtime grants applied")


if __name__ == "__main__":
    main()
