"""Inspect, prepare, budget, resume and explicitly activate an OpenAI shadow index."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from dotenv import dotenv_values
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import Settings  # noqa: E402
from backend.core.db import Database  # noqa: E402
from backend.core.postgres import PostgresDatabase  # noqa: E402
from backend.core.reliability import ServiceError  # noqa: E402
from backend.infrastructure.providers import Providers  # noqa: E402
from backend.infrastructure.vectors import VectorIndex  # noqa: E402
from backend.services.embedding_migration import EmbeddingMigration  # noqa: E402


async def execute(args):
    values = {**dotenv_values(args.env_file), **os.environ}
    source = Settings(_env_file=args.env_file)
    dsn = values.get(args.database_env, "")
    if dsn:
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1).replace("postgresql+psycopg://", "postgresql://", 1)
        db = PostgresDatabase(dsn)
        await db.init()
    else:
        # Never initialize a fresh database during a supposedly read-only inspection.
        path = source.data_dir / "research.sqlite3"
        if not args.sqlite or not path.is_file():
            raise ServiceError("请通过私密环境配置迁移数据库连接；SQLite 仅支持显式选择的已有数据库",
                               code="migration_database_required", retryable=False)
        db = Database(path)
    target = source.model_copy(update={"embedding_model": "text-embedding-3-large",
                                      "embedding_base_url": "https://api.openai.com/v1",
                                      "embedding_dimension": 3072, "send_embedding_dimensions": True,
                                      "embedding_api_key": SecretStr(values.get("OPENAI_API_KEY", "")),
                                      "demo_mode": False})
    providers, vectors = Providers(target, db), VectorIndex(target, db)
    tool = EmbeddingMigration(db, source, target, providers, vectors)
    try:
        if args.command == "inspect":
            return await tool.inspect()
        if args.command == "prepare":
            return await tool.prepare(maintenance_acknowledged=args.maintenance_acknowledged)
        if args.command == "progress":
            return await tool.progress(args.migration_id)
        if args.command == "run":
            if not target.embedding_api_key.get_secret_value():
                raise ServiceError("目标 OPENAI_API_KEY 未配置", code="provider_configuration", retryable=False)
            return await tool.run(args.migration_id, subject=args.subject, max_requests=args.max_requests,
                                  max_reserved_tokens=args.max_reserved_tokens, allow_paid=args.allow_paid,
                                  retry_failed=args.retry_failed, retry_unknown=args.retry_unknown)
        if args.command == "activate":
            return await tool.activate(args.migration_id, maintenance_acknowledged=args.maintenance_acknowledged)
        return await tool.abort(args.migration_id)
    finally:
        await providers.close()
        await vectors.close()
        if hasattr(db, "close"):
            await db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--database-env", choices=["MIGRATOR_DATABASE_URL", "DATABASE_URL", "PYTHON_DATABASE_URL"],
                        default="MIGRATOR_DATABASE_URL")
    parser.add_argument("--sqlite", action="store_true", help="Allow an existing local SQLite database; never creates one")
    parser.add_argument("--output", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--maintenance-acknowledged", action="store_true")
    for name in ("progress", "run", "activate", "abort"):
        command = commands.add_parser(name)
        command.add_argument("migration_id")
        if name == "activate":
            command.add_argument("--maintenance-acknowledged", action="store_true")
        if name == "run":
            command.add_argument("--subject", required=True)
            command.add_argument("--allow-paid", action="store_true")
            command.add_argument("--max-requests", required=True, type=int)
            command.add_argument("--max-reserved-tokens", required=True, type=int)
            command.add_argument("--retry-failed", action="store_true")
            command.add_argument("--retry-unknown", action="store_true")
    args = parser.parse_args()
    try:
        report = asyncio.run(execute(args))
        code = 0
    except ServiceError as error:
        report, code = {"error": error.code, "message": str(error)}, 1
    except Exception:
        report, code = {"error": "migration_unavailable", "message": "迁移检查失败；数据库、驱动与私密配置输出已隐藏"}, 1
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
