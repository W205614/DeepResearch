"""Persist document-scan recovery, external cleanup, and protected archives."""
from alembic import op
from backend.core.recovery_schema import SCHEMA

revision = '0014_runtime_reliability'
down_revision = '0013_evidence_lifecycle'
branch_labels = None
depends_on = None


def upgrade():
    for statement in SCHEMA.split(';'):
        if statement.strip():
            op.execute(statement)
    # A stopped upload has no in-memory owner. Recovery rechecks bytes and scans
    # again before indexing; absence of bytes never bypasses the scanner.
    op.execute("""INSERT INTO document_scan_jobs(document_id,user_id,object_key,state,updated_at)
        SELECT id,user_id,CASE WHEN object_key='' THEN id ELSE object_key END,'pending',updated_at
        FROM documents WHERE status='scanning' ON CONFLICT DO NOTHING""")


def downgrade():
    # Historical archives are evidence. Do not silently destroy them on downgrade.
    op.execute("DROP TABLE run_cleanup")
    op.execute("DROP TABLE document_scan_jobs")
