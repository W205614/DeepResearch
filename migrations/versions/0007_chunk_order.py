"""Keep original block order in document inspection; old indexes need reindexing."""
from alembic import op

revision = "0007_chunk_order"
down_revision = "0006_search_accounting"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE chunks ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 0")
    op.execute("DELETE FROM document_search_cache")


def downgrade():
    op.execute("ALTER TABLE chunks DROP COLUMN ordinal")
