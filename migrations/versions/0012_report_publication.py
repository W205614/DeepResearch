"""Java-owned, immutable research report snapshots and review state.

Revision ID: 0012_report_publication
Revises: 0011_document_hot_update
"""
from alembic import op


revision = "0012_report_publication"
down_revision = "0011_document_hot_update"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE threads ADD COLUMN IF NOT EXISTS created_by TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS report_submitted BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("""UPDATE threads t SET created_by=COALESCE((
        SELECT NULLIF(r.created_by,'') FROM runs r WHERE r.thread_id=t.id
        AND r.created_by<>'' ORDER BY r.created_at,r.id LIMIT 1), '')
        WHERE t.created_by=''""")
    op.execute("""CREATE TABLE report_publications(
        id TEXT PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        source_run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE RESTRICT,
        author_subject TEXT NOT NULL,
        topic TEXT NOT NULL,
        report_markdown TEXT NOT NULL,
        sources_json TEXT NOT NULL,
        validation_json TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','published','rejected','withdrawn')),
        submitted_at TEXT NOT NULL,
        reviewed_by TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT NOT NULL DEFAULT '',
        review_reason TEXT NOT NULL DEFAULT '',
        withdrawn_by TEXT NOT NULL DEFAULT '',
        withdrawn_at TEXT NOT NULL DEFAULT '',
        withdrawal_reason TEXT NOT NULL DEFAULT ''
    )""")
    op.execute("CREATE INDEX report_publications_workspace_status ON report_publications(workspace_id,status,submitted_at DESC)")


def downgrade():
    op.execute("DROP TABLE report_publications")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS report_submitted")
    op.execute("ALTER TABLE threads DROP COLUMN IF EXISTS created_by")
