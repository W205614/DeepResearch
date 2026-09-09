"""dead-letter workflow"""
from alembic import op

revision = "0002_dead_letter_runs"
down_revision = "0001_enterprise_workspace"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("CREATE TABLE IF NOT EXISTS dead_letter_runs(run_id TEXT PRIMARY KEY,user_id TEXT NOT NULL,category TEXT NOT NULL,message TEXT NOT NULL,failed_at TEXT NOT NULL,recovered_at TEXT DEFAULT '')")
    op.execute("CREATE INDEX IF NOT EXISTS dead_letter_user ON dead_letter_runs(user_id,failed_at DESC)")

def downgrade():
    op.execute("DROP TABLE IF EXISTS dead_letter_runs")