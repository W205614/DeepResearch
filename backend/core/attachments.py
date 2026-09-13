"""Schema shared by offline SQLite and the incremental PostgreSQL migration."""
ATTACHMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS attachments(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, owner_subject TEXT NOT NULL,
 name TEXT NOT NULL, media_type TEXT NOT NULL, size INTEGER NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, run_id TEXT NOT NULL DEFAULT '',
 position INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS attachments_run ON attachments(run_id,position);
CREATE INDEX IF NOT EXISTS attachments_expiry ON attachments(status,created_at);
CREATE TABLE IF NOT EXISTS run_vision(
 run_id TEXT PRIMARY KEY, result TEXT NOT NULL);
"""
