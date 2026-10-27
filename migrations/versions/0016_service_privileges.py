"""Runtime service privilege boundary; bootstrap/migrate jobs own DDL."""
from alembic import op

revision = "0016_service_privileges"
down_revision = "0015_usage_governance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


def downgrade():
    # Rollback must not silently reopen schema creation to all database users.
    pass
