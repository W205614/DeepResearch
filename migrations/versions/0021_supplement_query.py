"""Persist the chosen query for new supplement requests and replay."""
from alembic import op

revision = "0021_supplement_query"
down_revision = "0020_research_supplements"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE runs ADD COLUMN supplement_query TEXT")


def downgrade():
    op.execute("ALTER TABLE runs DROP COLUMN supplement_query")
