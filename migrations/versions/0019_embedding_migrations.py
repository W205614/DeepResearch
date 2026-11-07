"""Resumable shadow-vector migration receipts without source text or credentials."""
from alembic import op

from backend.core.platform_schema import EMBEDDING_SCHEMA

revision = "0019_embedding_migrations"
down_revision = "0018_platform_governance"
branch_labels = None
depends_on = None


def upgrade():
    for statement in EMBEDDING_SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError("Index migration receipts must be retained until an explicit recovery decision")
