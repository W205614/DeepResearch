"""ACL inheritance, confirmed research scope, source checks and bounded lexical postings."""
from alembic import op
from backend.core.product_schema import SCHEMA
revision = '0017_product_governance'
down_revision = '0016_service_privileges'
branch_labels = None
depends_on = None

def upgrade():
    for statement in SCHEMA.split(';'):
        if statement.strip():
            op.execute(statement)
    op.execute("INSERT INTO document_policies(document_id,workspace_id,owner_subject) SELECT d.id,d.user_id,COALESCE(w.created_by,d.user_id) FROM documents d LEFT JOIN workspaces w ON w.id=d.user_id ON CONFLICT DO NOTHING")

def downgrade():
    # Preserve permission metadata on application rollback; destructive schema rollback is explicit.
    raise RuntimeError('Permission metadata must be retained; restore a verified backup for schema downgrade')
