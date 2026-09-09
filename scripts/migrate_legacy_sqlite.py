"""Migrate a legacy SQLite research store into the enterprise PostgreSQL store.

The migration is deliberately fail-closed: every legacy subject must be mapped to an
OIDC subject before records are copied. It never prints document, report, or secret text.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import asyncpg

from backend.core.postgres import POSTGRES_SCHEMA

TABLES = (
    "threads", "runs", "documents", "chunks", "memories", "counters",
    "search_cache", "web_cache", "document_search_cache", "metadata",
)
USER_TABLES = {"threads", "runs", "documents", "chunks", "memories", "document_search_cache"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely migrate legacy SQLite data to PostgreSQL.")
    parser.add_argument("--source", type=Path, required=True, help="Legacy research.sqlite3 path")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""), help="PostgreSQL URL (defaults to DATABASE_URL; never logged)")
    parser.add_argument("--subject-map", type=Path, required=True, help="JSON object: legacy subject -> OIDC subject")
    parser.add_argument("--report", type=Path, default=Path(".cache/legacy-migration-report.json"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_map(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value:
        raise ValueError("subject map must be a non-empty JSON object")
    result = {str(old): str(new) for old, new in value.items()}
    if any(not old or not new or len(new) > 128 for old, new in result.items()):
        raise ValueError("subject map contains an empty or invalid OIDC subject")
    return result


def rows(connection: sqlite3.Connection, table: str) -> list[dict]:
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')] if exists else []


def legacy_subjects(data: dict[str, list[dict]]) -> set[str]:
    return {str(row["user_id"]) for table in USER_TABLES for row in data.get(table, []) if row.get("user_id")}


def workspace_id(subject: str) -> str:
    return "legacy-" + hashlib.sha256(subject.encode()).hexdigest()[:24]


async def migrate(data: dict[str, list[dict]], mapping: dict[str, str], dsn: str) -> dict[str, int]:
    connection = await asyncpg.connect(dsn)
    copied: dict[str, int] = {}
    try:
        for statement in POSTGRES_SCHEMA.split(";"):
            if statement.strip():
                await connection.execute(statement)
        for subject in sorted(set(mapping.values())):
            workspace = workspace_id(subject)
            await connection.execute("INSERT INTO workspaces(id,name,created_by,created_at) VALUES($1,$2,$3,NOW()::text) ON CONFLICT DO NOTHING", workspace, f"迁移工作空间-{subject[:32]}", subject)
            await connection.execute("INSERT INTO memberships(workspace_id,subject,role,created_at) VALUES($1,$2,'admin',NOW()::text) ON CONFLICT DO NOTHING", workspace, subject)
            await connection.execute("INSERT INTO workspace_limits(workspace_id,daily_search_limit,daily_token_limit,concurrent_run_limit) VALUES($1,120,0,2) ON CONFLICT DO NOTHING", workspace)
        for table, source_rows in data.items():
            if not source_rows:
                copied[table] = 0
                continue
            count = 0
            for row in source_rows:
                row = dict(row)
                if "user_id" in row:
                    row["user_id"] = mapping[row["user_id"]]
                if table == "events":
                    continue
                columns = list(row)
                placeholders = ",".join(f"${index}" for index in range(1, len(columns) + 1))
                query = f'INSERT INTO "{table}" ({",".join(columns)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'
                await connection.execute(query, *(row[column] for column in columns))
                count += 1
            copied[table] = count
        for event in data.get("events", []):
            await connection.execute("INSERT INTO events(run_id,type,data,created_at) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING", event["run_id"], event["type"], event["data"], event["created_at"])
        copied["events"] = len(data.get("events", []))
    finally:
        await connection.close()
    return copied


async def main() -> int:
    args = parse_args()
    if not args.source.is_file():
        raise SystemExit("legacy SQLite source does not exist")
    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required")
    mapping = load_map(args.subject_map)
    source = sqlite3.connect(args.source)
    source.row_factory = sqlite3.Row
    try:
        data = {table: rows(source, table) for table in TABLES}
        data["events"] = rows(source, "events")
    finally:
        source.close()
    missing = sorted(legacy_subjects(data) - set(mapping))
    report = {"source": str(args.source), "dry_run": args.dry_run, "table_rows": {table: len(value) for table, value in data.items()}, "unmapped_subjects": missing}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if missing:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise SystemExit("migration refused: map every legacy subject to an OIDC subject; see report")
    if not args.dry_run:
        report["copied_rows"] = await migrate(data, mapping, args.database_url)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "dry-run" if args.dry_run else "migrated", "report": str(args.report), "tables": report["table_rows"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))