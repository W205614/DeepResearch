"""Read-only database capacity and host-probe metrics, without a Docker socket."""
import asyncio
import json
import os
from pathlib import Path
import time
import math

import asyncpg
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse


def host_metrics(path, clock=time.time):
    try:
        status = json.loads(Path(path).read_text(encoding="utf-8"))
        if status.get("version") != 1:
            raise ValueError()
    except (OSError, ValueError, TypeError):
        status = {}
    def timestamp(value):
        try:
            parsed = float(value)
            return parsed if math.isfinite(parsed) and 0 <= parsed <= clock() + 300 else 0.0
        except (ValueError, TypeError):
            return 0.0
    observed = timestamp(status.get("observed_at", 0))
    verified = timestamp(status.get("last_verified_backup_at", 0))
    try:
        invalid = max(0, int(status.get('invalid_backup_count', 0)))
    except (ValueError, TypeError, OverflowError):
        invalid = 1
    rows = [f"deepresearch_host_probe_timestamp_seconds {observed}",
            f"deepresearch_backup_verified_timestamp_seconds {verified}",
            f"deepresearch_backup_invalid_count {invalid}"]
    for name, value in status.get("disks", {}).items():
        # Never export a path supplied in host configuration.
        import re
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", name):
            continue
        try:
            unavailable = value.get("unavailable") or not 0 <= int(value["free_bytes"]) <= int(value["total_bytes"])
        except (ValueError, TypeError, KeyError):
            unavailable = True
        rows.append(f'deepresearch_host_disk_available{{disk="{name}"}} {0 if unavailable else 1}')
        if not unavailable:
            for key in ("free_bytes", "total_bytes"):
                rows.append(f'deepresearch_host_disk_{key}{{disk="{name}"}} {int(value[key])}')
    return rows


def create_app():
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    rpo = max(60, int(os.environ.get("BACKUP_RPO_SECONDS", "86400")))
    rto = max(60, int(os.environ.get("BACKUP_RTO_SECONDS", "900")))

    @app.get("/livez")
    async def live():
        return {"status": "alive"}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics():
        rows = host_metrics(os.environ.get("HOST_STATUS_FILE", "/host-metrics/status.json"))
        rows += [f"deepresearch_backup_rpo_target_seconds {rpo}", f"deepresearch_backup_rto_target_seconds {rto}"]
        conn = None
        try:
            async with asyncio.timeout(4):
                conn = await asyncpg.connect(os.environ["MONITOR_DATABASE_URL"], timeout=3)
                size = await conn.fetchval("SELECT pg_database_size(current_database())")
                connections = await conn.fetchval("SELECT count(*) FROM pg_stat_activity")
                maximum = int(await conn.fetchval("SHOW max_connections"))
                queued = await conn.fetchval("SELECT count(*) FROM runs WHERE status IN ('queued','interrupted')")
                documents = await conn.fetchval("SELECT count(*) FROM documents WHERE status IN ('scanning','indexing','rebuilding')")
            rows += ["deepresearch_ops_database_up 1", f"deepresearch_database_size_bytes {size}",
                     f"deepresearch_database_connections {connections}", f"deepresearch_database_max_connections {maximum}",
                     f"deepresearch_pending_documents {documents}", f"deepresearch_persisted_pending_runs {queued}"]
        except Exception:
            rows.append("deepresearch_ops_database_up 0")
        finally:
            if conn:
                await conn.close()
        return "\n".join(rows) + "\n"

    return app
