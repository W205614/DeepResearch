"""Version document objects for safe hot replacement.

Revision ID: 0011_document_hot_update
Revises: 0010_outbox_leases
"""
from alembic import op


revision = "0011_document_hot_update"
down_revision = "0010_outbox_leases"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("updated_at", "object_key", "pending_name", "pending_hash", "pending_object_key"):
        op.execute(f"ALTER TABLE documents ADD COLUMN IF NOT EXISTS {name} TEXT NOT NULL DEFAULT ''")
    op.execute("UPDATE documents SET updated_at=COALESCE(created_at,'') WHERE updated_at=''")
    op.execute("ALTER TABLE document_cleanup ADD COLUMN IF NOT EXISTS object_key TEXT NOT NULL DEFAULT ''")
    op.execute("UPDATE document_cleanup SET object_key=document_id WHERE object_key=''")


def downgrade():
    op.execute("ALTER TABLE document_cleanup DROP COLUMN IF EXISTS object_key")
    for name in ("pending_object_key", "pending_hash", "pending_name", "object_key", "updated_at"):
        op.execute(f"ALTER TABLE documents DROP COLUMN IF EXISTS {name}")
