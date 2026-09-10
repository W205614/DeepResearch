"""Version document writes and preserve daily search consumption."""
from alembic import op
from backend.core.consistency import DAILY_SCHEMA

revision = "0004_consistency"
down_revision = "0003_personal_memory_ownership"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE documents ADD COLUMN index_version INTEGER NOT NULL DEFAULT 0")
    for sql in DAILY_SCHEMA.split(";"):
        if sql.strip():
            op.execute(sql)
    op.execute("""INSERT INTO workspace_daily_usage(workspace_id,day,search_calls)
        SELECT r.user_id,substr(r.created_at,1,10),SUM(c.search_calls)
        FROM runs r JOIN counters c ON c.run_id=r.id GROUP BY r.user_id,substr(r.created_at,1,10)
        ON CONFLICT DO NOTHING""")


def downgrade():
    op.execute("DROP TABLE document_cleanup")
    op.execute("DROP TABLE workspace_daily_usage")
    op.execute("ALTER TABLE documents DROP COLUMN index_version")
