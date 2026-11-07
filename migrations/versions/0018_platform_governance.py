"""Platform account revocation, immutable governance audit and current workspace ownership."""
from alembic import op

from backend.core.platform_schema import PLATFORM_SCHEMA

revision = "0018_platform_governance"
down_revision = "0017_product_governance"
branch_labels = None
depends_on = None


def upgrade():
    for statement in PLATFORM_SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)
    op.execute("ALTER TABLE workspaces ADD COLUMN owner_subject TEXT NOT NULL DEFAULT ''")
    op.execute("UPDATE workspaces SET owner_subject=created_by WHERE owner_subject=''")
    op.execute("""INSERT INTO platform_users(subject,display_name,created_at,updated_at)
        SELECT subject,subject,to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'),
               to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')
        FROM (SELECT subject FROM memberships UNION SELECT created_by FROM workspaces
              UNION SELECT created_by FROM runs) known WHERE subject<>'' ON CONFLICT DO NOTHING""")


def downgrade():
    raise RuntimeError("Account revocation and ownership metadata must be retained; restore an approved recovery set")
