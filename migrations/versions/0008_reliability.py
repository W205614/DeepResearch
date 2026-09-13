"""Durable budgets, provider health and verified artifacts; additive upgrade."""
from alembic import op
from backend.core.reliability import RUN_COLUMNS, SCHEMA

revision = "0008_reliability"
down_revision = "0007_chunk_order"
branch_labels = None
depends_on = None


def upgrade():
    for name, definition in RUN_COLUMNS.items():
        op.execute(f"ALTER TABLE runs ADD COLUMN {name} {definition}")
    op.execute("ALTER TABLE documents ADD COLUMN pending_version INTEGER NOT NULL DEFAULT 0")
    for statement in SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    for table in ("verified_claims", "call_attempts", "provider_health"):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE documents DROP COLUMN pending_version")
    for name in RUN_COLUMNS:
        op.execute(f"ALTER TABLE runs DROP COLUMN {name}")
