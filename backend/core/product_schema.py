"""Versioned product metadata; Java owns ACL/intake, Python owns derived retrieval/checks."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,name TEXT NOT NULL,visibility TEXT NOT NULL DEFAULT 'workspace',created_by TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS collections_workspace ON collections(workspace_id,id);
CREATE TABLE IF NOT EXISTS collection_grants(collection_id TEXT NOT NULL,subject TEXT NOT NULL,permission TEXT NOT NULL,PRIMARY KEY(collection_id,subject));
CREATE TABLE IF NOT EXISTS document_policies(document_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,collection_id TEXT NOT NULL DEFAULT '',owner_subject TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS document_policy_collection ON document_policies(workspace_id,collection_id);
CREATE TABLE IF NOT EXISTS research_intakes(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,subject TEXT NOT NULL,request_json TEXT NOT NULL,status TEXT NOT NULL,answers_json TEXT NOT NULL DEFAULT '{}',expires_at BIGINT NOT NULL,run_id TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS intakes_owner ON research_intakes(workspace_id,subject,status);
CREATE TABLE IF NOT EXISTS source_rechecks(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,report_id TEXT NOT NULL,source_id TEXT NOT NULL,status TEXT NOT NULL,old_hash TEXT NOT NULL,new_hash TEXT NOT NULL DEFAULT '',checked_at TEXT NOT NULL,actor_subject TEXT NOT NULL,error_code TEXT NOT NULL DEFAULT '',snapshot_json TEXT NOT NULL DEFAULT '{}');
CREATE INDEX IF NOT EXISTS source_rechecks_report ON source_rechecks(workspace_id,report_id,checked_at);
CREATE TABLE IF NOT EXISTS lexical_chunks(chunk_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,document_id TEXT NOT NULL,content_hash TEXT NOT NULL,length INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS lexical_workspace ON lexical_chunks(workspace_id,document_id);
CREATE TABLE IF NOT EXISTS chunk_terms(workspace_id TEXT NOT NULL,term TEXT NOT NULL,chunk_id TEXT NOT NULL,frequency INTEGER NOT NULL,PRIMARY KEY(workspace_id,term,chunk_id));
CREATE INDEX IF NOT EXISTS terms_chunk ON chunk_terms(chunk_id);
"""
