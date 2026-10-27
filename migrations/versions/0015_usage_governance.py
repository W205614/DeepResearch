"""Persistent cross-task paid-call governance and diagnostic cooldown."""
from alembic import op
from backend.core.usage_governance import USAGE_SCHEMA

revision = "0015_usage_governance"
down_revision = "0014_runtime_reliability"
branch_labels = None
depends_on = None


def upgrade():
    for statement in USAGE_SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    for name in ("provider_probes", "usage_attempts", "usage_periods"):
        op.execute(f"DROP TABLE {name}")
