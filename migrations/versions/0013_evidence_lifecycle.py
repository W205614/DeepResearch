"""Immutable document/evidence versions and report revision lifecycle."""
from alembic import op
from backend.core.lifecycle_schema import SCHEMA, PUBLICATION_COLUMNS, PUBLICATION_INDEXES
revision = '0013_evidence_lifecycle'
down_revision = '0012_report_publication'
branch_labels = None
depends_on = None

def upgrade():
    for statement in SCHEMA.split(';'):
        if statement.strip():
            op.execute(statement)
    for name, definition in PUBLICATION_COLUMNS.items():
        op.execute(f'ALTER TABLE report_publications ADD COLUMN {name} {definition}')
    op.execute("INSERT INTO workspace_corpus_revisions(workspace_id,revision) SELECT id,0 FROM workspaces ON CONFLICT DO NOTHING")
    op.execute("INSERT INTO report_series(id,workspace_id,current_report_id,created_at,updated_at) SELECT id,workspace_id,CASE WHEN status='published' THEN id ELSE NULL END,submitted_at,submitted_at FROM report_publications")
    op.execute("UPDATE report_publications SET series_id=id")
    op.execute("UPDATE report_publications p SET corpus_revision=-1 FROM runs r WHERE r.id=p.source_run_id AND r.data_policy='public'")
    for statement in PUBLICATION_INDEXES.split(';'):
        if statement.strip():
            op.execute(statement)

def downgrade():
    op.execute("DROP INDEX IF EXISTS report_publications_series_version")
    op.execute("DROP INDEX IF EXISTS report_publications_page")
    for name in reversed(PUBLICATION_COLUMNS):
        op.execute(f'ALTER TABLE report_publications DROP COLUMN {name}')
    for table in ('report_revision_jobs','report_series','report_impacts','run_artifacts','document_change_events','document_versions','workspace_corpus_revisions'):
        op.execute(f'DROP TABLE {table}')
