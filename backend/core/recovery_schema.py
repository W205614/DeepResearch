"""Durable reconciliation records shared by PostgreSQL and offline SQLite."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS document_scan_jobs(
 document_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,object_key TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
 error_code TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS document_scan_pending ON document_scan_jobs(state,updated_at);
CREATE TABLE IF NOT EXISTS run_cleanup(
 run_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,created_at TEXT NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,error_code TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS retention_archives(
 kind TEXT NOT NULL,source_id TEXT NOT NULL,workspace_id TEXT NOT NULL,
 payload_json TEXT NOT NULL,content_sha256 TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(kind,source_id));
"""
