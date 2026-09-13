"""Persist task image attachments separately from searchable documents."""
from alembic import op
from backend.core.attachments import ATTACHMENT_SCHEMA

revision = "0005_image_attachments"
down_revision = "0004_consistency"
branch_labels = None
depends_on = None


def upgrade():
    for statement in ATTACHMENT_SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    op.execute("DROP TABLE run_vision")
    op.execute("DROP TABLE attachments")
