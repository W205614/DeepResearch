"""store personal memory ownership and research initiator

Revision ID: 0003_personal_memory_ownership
Revises: 0002_dead_letter_runs
"""
from alembic import op


revision = "0003_personal_memory_ownership"
down_revision = "0002_dead_letter_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS created_by TEXT NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS owner_subject TEXT NOT NULL DEFAULT ''")
    op.execute("""UPDATE runs SET created_by=COALESCE(
        (SELECT created_by FROM workspaces WHERE id=runs.user_id),user_id)
        WHERE created_by=''""")
    # Older workspace-scoped personal records do not retain their actor. Assign
    # them to the workspace creator rather than expose them to every member.
    op.execute("""UPDATE memories SET owner_subject=COALESCE(
        (SELECT created_by FROM workspaces WHERE id=memories.user_id),user_id)
        WHERE owner_subject='' AND kind IN ('profile','preference')""")
    op.execute("CREATE INDEX IF NOT EXISTS personal_memory_owner ON memories(owner_subject,kind,created_at)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS personal_memory_owner")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS owner_subject")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS created_by")
