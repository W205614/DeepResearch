"""Bounded cache expiry and optional immutable archives; evidence stays protected."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from ..core.db import now


class Retention:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    async def reconcile(self):
        batch = getattr(self.settings, 'retention_cleanup_batch_size', 100)
        days = getattr(self.settings, 'retention_cache_days', 7)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        deleted = {}
        # Only expendable caches expire automatically. Run history, source
        # versions, events, checkpoints, personal memories and publications do not.
        for table, key, stamp in (('web_cache', 'url', 'fetched_at'),
                                  ('document_search_cache', None, 'created_at')):
            if key:
                count = await self.db.execute(f"DELETE FROM {table} WHERE {key} IN (SELECT {key} FROM {table} WHERE {stamp}<? ORDER BY {stamp},{key} LIMIT ?)",
                                              (cutoff, batch))
            else:
                count = await self.db.execute("""DELETE FROM document_search_cache WHERE
                    (user_id,corpus_hash,query_hash,limit_value) IN
                    (SELECT user_id,corpus_hash,query_hash,limit_value FROM document_search_cache
                    WHERE created_at<? ORDER BY created_at,user_id,query_hash LIMIT ?)""", (cutoff, batch))
            deleted[table] = count
        for table in ('run_artifacts', 'dead_letter_runs'):
            # These rows predate durable deletion and have no independent retention
            # contract. A publication cannot reference a missing run (FK RESTRICT).
            deleted[table + '_orphans'] = await self.db.execute(f"""DELETE FROM {table} WHERE run_id IN
                (SELECT a.run_id FROM {table} a LEFT JOIN runs r ON r.id=a.run_id
                 WHERE r.id IS NULL ORDER BY a.run_id LIMIT ?)""", (batch,))
        archive_days = getattr(self.settings, 'retention_archive_after_days', 0)
        archived = 0
        if archive_days:
            archive_cutoff = (datetime.now(timezone.utc) - timedelta(days=archive_days)).isoformat()
            rows = await self.db.rows("""SELECT r.id,r.user_id,r.topic,r.status,r.report,r.sources,r.validation,
                a.requirements_json,a.claims_json FROM runs r JOIN run_artifacts a ON a.run_id=r.id
                WHERE r.status IN ('completed','insufficient','failed','cancelled') AND r.updated_at<?
                AND NOT EXISTS(SELECT 1 FROM retention_archives x WHERE x.kind='research' AND x.source_id=r.id)
                ORDER BY r.updated_at,r.id LIMIT ?""", (archive_cutoff, batch))
            for row in rows:
                payload = json.dumps(row, sort_keys=True, ensure_ascii=False)
                if len(payload.encode('utf-8')) > 4 * 1024 * 1024:
                    continue  # Keep the source intact; never truncate evidence.
                archived += await self.db.execute("""INSERT OR IGNORE INTO retention_archives
                    (kind,source_id,workspace_id,payload_json,content_sha256,created_at)
                    VALUES('research',?,?,?,?,?)""", (row['id'], row['user_id'], payload,
                    hashlib.sha256(payload.encode()).hexdigest(), now()))
        return {'cache_deleted': deleted, 'archived': archived, 'source_evidence_deleted': 0,
                'policy': {'batch': batch, 'cache_days': days, 'archive_after_days': archive_days,
                           'source_versions': 'protected', 'events': 'explicit_run_deletion_only',
                           'checkpoints': 'explicit_run_deletion_only', 'saved_memories': 'independent'}}
