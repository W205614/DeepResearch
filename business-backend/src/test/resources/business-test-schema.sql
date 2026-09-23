DROP TABLE IF EXISTS business_outbox CASCADE;
DROP TABLE IF EXISTS report_publications CASCADE;
DROP TABLE IF EXISTS dead_letter_runs CASCADE;
DROP TABLE IF EXISTS audit_logs CASCADE;
DROP TABLE IF EXISTS counters CASCADE;
DROP TABLE IF EXISTS attachments CASCADE;
DROP TABLE IF EXISTS chunks CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS events CASCADE;
DROP TABLE IF EXISTS runs CASCADE;
DROP TABLE IF EXISTS threads CASCADE;
DROP TABLE IF EXISTS workspace_limits CASCADE;
DROP TABLE IF EXISTS memberships CASCADE;
DROP TABLE IF EXISTS workspaces CASCADE;

CREATE TABLE workspaces(
    id TEXT PRIMARY KEY,name TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE memberships(
    workspace_id TEXT NOT NULL,subject TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id,subject),CHECK(role IN ('admin','researcher','viewer'))
);
CREATE TABLE workspace_limits(
    workspace_id TEXT PRIMARY KEY,daily_search_limit INTEGER NOT NULL,
    daily_token_limit INTEGER NOT NULL,concurrent_run_limit INTEGER NOT NULL
);
CREATE TABLE threads(
    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,title TEXT,created_at TEXT,thread_key TEXT,
    created_by TEXT NOT NULL DEFAULT '',
    UNIQUE(user_id,thread_key)
);
CREATE TABLE runs(
    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,thread_id TEXT NOT NULL,topic TEXT,mode TEXT,status TEXT,
    created_at TEXT,updated_at TEXT,report TEXT DEFAULT '',sources TEXT DEFAULT '[]',validation TEXT DEFAULT '{}',
    error TEXT DEFAULT '',client_request_id TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,last_attempt_at TEXT DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',deadline_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    call_attempts INTEGER NOT NULL DEFAULT 0,reserved_tokens INTEGER NOT NULL DEFAULT 0,
    auto_recoveries INTEGER NOT NULL DEFAULT 0,error_info TEXT NOT NULL DEFAULT '{}',
    data_policy TEXT NOT NULL DEFAULT 'internal',report_submitted BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(user_id,client_request_id)
);
CREATE TABLE report_publications(
    id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    source_run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE RESTRICT,
    author_subject TEXT NOT NULL,topic TEXT NOT NULL,report_markdown TEXT NOT NULL,
    sources_json TEXT NOT NULL,validation_json TEXT NOT NULL,content_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','published','rejected','withdrawn')),
    submitted_at TEXT NOT NULL,reviewed_by TEXT NOT NULL DEFAULT '',reviewed_at TEXT NOT NULL DEFAULT '',
    review_reason TEXT NOT NULL DEFAULT '',withdrawn_by TEXT NOT NULL DEFAULT '',
    withdrawn_at TEXT NOT NULL DEFAULT '',withdrawal_reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX one_active_thread ON runs(thread_id) WHERE status IN ('queued','running');
CREATE TABLE events(
    id BIGSERIAL PRIMARY KEY,run_id TEXT NOT NULL,type TEXT,data TEXT,created_at TEXT
);
CREATE TABLE attachments(
    id TEXT PRIMARY KEY,user_id TEXT NOT NULL,owner_subject TEXT NOT NULL,name TEXT NOT NULL,
    media_type TEXT NOT NULL,size INTEGER NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE documents(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,status TEXT NOT NULL,
    hash TEXT NOT NULL,index_version INTEGER NOT NULL);
CREATE TABLE chunks(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,user_id TEXT NOT NULL,text TEXT NOT NULL);
CREATE TABLE counters(
    run_id TEXT PRIMARY KEY,search_calls INTEGER DEFAULT 0,llm_calls INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,completion_tokens INTEGER DEFAULT 0
);
CREATE TABLE audit_logs(
    id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,actor_subject TEXT NOT NULL,action TEXT NOT NULL,
    target_type TEXT NOT NULL,target_id TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE dead_letter_runs(
    run_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,category TEXT NOT NULL,message TEXT NOT NULL,
    failed_at TEXT NOT NULL,recovered_at TEXT DEFAULT ''
);
CREATE TABLE business_outbox(
    id TEXT PRIMARY KEY,aggregate_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,updated_at TEXT NOT NULL,lease_owner TEXT NOT NULL DEFAULT '',
    lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',
    delivered_at TEXT NOT NULL DEFAULT '',
    CONSTRAINT business_outbox_status CHECK(status IN ('pending','processing','delivered','dead'))
);
CREATE UNIQUE INDEX business_outbox_active_command
    ON business_outbox(aggregate_id,event_type) WHERE status IN ('pending','processing');
CREATE INDEX business_outbox_pending
    ON business_outbox(status,next_attempt_at,lease_until,created_at);
