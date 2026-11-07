"""Shared platform governance and derived-index migration persistence contracts."""

PLATFORM_SCHEMA = """
CREATE TABLE IF NOT EXISTS platform_users(
 subject TEXT PRIMARY KEY,status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 revoked_before BIGINT NOT NULL DEFAULT 0,display_name TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS platform_audit_logs(
 id TEXT PRIMARY KEY,actor_subject TEXT NOT NULL,action TEXT NOT NULL,target_type TEXT NOT NULL,
 target_id TEXT NOT NULL,reason TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS platform_user_cursor ON platform_users(created_at,subject);
CREATE INDEX IF NOT EXISTS platform_audit_cursor ON platform_audit_logs(created_at,id);
CREATE INDEX IF NOT EXISTS platform_audit_actor ON platform_audit_logs(actor_subject,created_at);
"""

EMBEDDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_migrations(
 id TEXT PRIMARY KEY,source_fingerprint TEXT NOT NULL,source_snapshot TEXT NOT NULL,
 target_fingerprint TEXT NOT NULL,target_config_json TEXT NOT NULL,target_prefix TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL DEFAULT 'prepared',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 activated_at TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS embedding_migration_items(
 migration_id TEXT NOT NULL,kind TEXT NOT NULL,item_id TEXT NOT NULL,workspace_id TEXT NOT NULL,
 source_hash TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
 reserved_tokens BIGINT NOT NULL DEFAULT 0,
 error_code TEXT NOT NULL DEFAULT '',updated_at TEXT NOT NULL,
 PRIMARY KEY(migration_id,kind,item_id),FOREIGN KEY(migration_id) REFERENCES embedding_migrations(id));
CREATE INDEX IF NOT EXISTS embedding_migration_progress ON embedding_migration_items(migration_id,status,kind,item_id);
"""
