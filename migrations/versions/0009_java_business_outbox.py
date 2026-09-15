"""durable Java business-command outbox

Revision ID: 0009_java_business_outbox
Revises: 0008_reliability
"""
from alembic import op


revision = "0009_java_business_outbox"
down_revision = "0008_reliability"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE IF NOT EXISTS business_outbox(
        id TEXT PRIMARY KEY,
        aggregate_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at DOUBLE PRECISION NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    op.execute("CREATE INDEX IF NOT EXISTS business_outbox_pending ON business_outbox(status,next_attempt_at,created_at)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS business_outbox")
