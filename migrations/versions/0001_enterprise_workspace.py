"""enterprise workspace baseline

Revision ID: 0001_enterprise_workspace
Revises:
Create Date: 2026-09-05
"""
from alembic import op
from sqlalchemy import text

from backend.core.postgres import POSTGRES_SCHEMA

revision = "0001_enterprise_workspace"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for statement in POSTGRES_SCHEMA.split(";"):
        if statement.strip():
            bind.execute(text(statement))


def downgrade():
    for table in ("audit_logs", "workspace_limits", "memberships", "workspaces", "metadata",
                  "web_cache", "search_cache", "counters", "memories", "chunks", "documents",
                  "events", "runs", "threads"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
