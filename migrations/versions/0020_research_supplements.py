"""Track related research and the immutable basis of targeted supplements."""
from alembic import op

revision = "0020_research_supplements"
down_revision = "0019_embedding_migrations"
branch_labels = None
depends_on = None

COLUMNS = ("related_run_id", "supplement_requirement_id", "supplement_url", "supplement_basis")


def upgrade():
    for column in COLUMNS:
        op.execute(f"ALTER TABLE runs ADD COLUMN {column} TEXT")


def downgrade():
    for column in reversed(COLUMNS):
        op.execute(f"ALTER TABLE runs DROP COLUMN {column}")
