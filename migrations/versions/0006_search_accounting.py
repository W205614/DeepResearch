"""Retain usage accounting without daily query or token quotas."""
from alembic import op

revision = "0006_search_accounting"
down_revision = "0005_image_attachments"
branch_labels = None
depends_on = None


def upgrade():
    # Keep legacy columns so existing installations/clients remain compatible.
    op.execute("UPDATE workspace_limits SET daily_search_limit=0,daily_token_limit=0")
    op.execute("DELETE FROM document_search_cache")


def downgrade():
    # Reverting schema history must not silently impose new user quotas.
    pass
