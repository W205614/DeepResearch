"""Shared immutable evidence and report lifecycle schema (SQLite/PostgreSQL)."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_corpus_revisions(workspace_id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS document_versions(document_id TEXT NOT NULL,user_id TEXT NOT NULL,version INTEGER NOT NULL,hash TEXT NOT NULL,name TEXT NOT NULL,object_key TEXT NOT NULL,status TEXT NOT NULL,blocks_json TEXT NOT NULL DEFAULT '[]',chunks_json TEXT NOT NULL DEFAULT '[]',parser_config TEXT NOT NULL DEFAULT '{}',index_config TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,PRIMARY KEY(document_id,version));
CREATE INDEX IF NOT EXISTS document_versions_owner ON document_versions(user_id,document_id,version);
CREATE TABLE IF NOT EXISTS document_change_events(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,document_id TEXT NOT NULL,from_version INTEGER NOT NULL,to_version INTEGER NOT NULL,revision INTEGER NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',error TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,processed_at TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS document_events_pending ON document_change_events(status,created_at);
CREATE TABLE IF NOT EXISTS run_artifacts(run_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,attempt_count INTEGER NOT NULL,corpus_revision INTEGER NOT NULL,requirements_json TEXT NOT NULL,claims_json TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS report_impacts(report_id TEXT NOT NULL,target_revision INTEGER NOT NULL,status TEXT NOT NULL,reasons_json TEXT NOT NULL,affected_claims_json TEXT NOT NULL,diff_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(report_id,target_revision));
CREATE TABLE IF NOT EXISTS report_series(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,current_report_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS report_revision_jobs(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,source_report_id TEXT NOT NULL,target_run_id TEXT NOT NULL UNIQUE,idempotency_key TEXT NOT NULL,requested_by TEXT NOT NULL,target_corpus_revision INTEGER NOT NULL,source_content_sha256 TEXT NOT NULL,status TEXT NOT NULL,plan_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(workspace_id,requested_by,idempotency_key));
CREATE INDEX IF NOT EXISTS report_revision_jobs_source ON report_revision_jobs(source_report_id,created_at);
"""
PUBLICATION_COLUMNS = {
    'series_id': 'TEXT DEFAULT NULL', 'parent_report_id': 'TEXT DEFAULT NULL',
    'revision_no': 'INTEGER NOT NULL DEFAULT 1', 'artifacts_json': "TEXT NOT NULL DEFAULT '{}'",
    'corpus_revision': 'INTEGER NOT NULL DEFAULT 0', 'evidence_status': "TEXT NOT NULL DEFAULT 'legacy'",
}
SQLITE_PUBLICATIONS = """CREATE TABLE IF NOT EXISTS report_publications(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,source_run_id TEXT NOT NULL UNIQUE,author_subject TEXT NOT NULL,topic TEXT NOT NULL,report_markdown TEXT NOT NULL,sources_json TEXT NOT NULL,validation_json TEXT NOT NULL,content_sha256 TEXT NOT NULL,status TEXT NOT NULL,submitted_at TEXT NOT NULL,reviewed_by TEXT NOT NULL DEFAULT '',reviewed_at TEXT NOT NULL DEFAULT '',review_reason TEXT NOT NULL DEFAULT '',withdrawn_by TEXT NOT NULL DEFAULT '',withdrawn_at TEXT NOT NULL DEFAULT '',withdrawal_reason TEXT NOT NULL DEFAULT '')"""

PUBLICATION_INDEXES = """CREATE INDEX IF NOT EXISTS report_publications_page ON report_publications(workspace_id,status,submitted_at,id);
CREATE UNIQUE INDEX IF NOT EXISTS report_publications_series_version ON report_publications(series_id,revision_no);"""
