"""SQL-authoritative document access, also applied to vectors, cached hits and evidence."""
from ..core.reliability import ServiceError
from ..core.usage_governance import paid_identity


def subject_for(workspace):
    identity = paid_identity()
    return identity[1] if identity and identity[0] == workspace else "" if identity else workspace


def access_sql(alias='d'):
    # Missing policies inherit workspace permission for backward-compatible migration.
    return f"""(EXISTS(SELECT 1 FROM memberships am WHERE am.workspace_id={alias}.user_id AND am.subject=? AND am.role='admin')
        OR NOT EXISTS(SELECT 1 FROM document_policies ap WHERE ap.document_id={alias}.id)
        OR EXISTS(SELECT 1 FROM document_policies ap LEFT JOIN collections ac ON ac.id=ap.collection_id AND ac.workspace_id=ap.workspace_id
            WHERE ap.document_id={alias}.id AND ap.workspace_id={alias}.user_id
            AND (ap.collection_id='' OR ac.visibility='workspace' OR ap.owner_subject=?
            OR EXISTS(SELECT 1 FROM collection_grants ag WHERE ag.collection_id=ap.collection_id AND ag.subject=?))))"""


def access_args(workspace):
    return (subject_for(workspace),) * 3


async def require_document(db, workspace, document_id, write=False):
    identity = paid_identity()
    if identity and identity[0] != workspace:
        raise ServiceError('资料不存在或访问权限已撤销', code='source_permission_denied', retryable=False)
    subject = subject_for(workspace)
    row = await db.one(f"SELECT d.id FROM documents d WHERE d.id=? AND d.user_id=? AND {access_sql()}",
                       (document_id, workspace, *access_args(workspace)))
    if not row:
        raise ServiceError('资料不存在或访问权限已撤销', code='source_permission_denied', retryable=False)
    if write:
        policy = await db.one('SELECT * FROM document_policies WHERE document_id=?', (document_id,))
        member = await db.membership(workspace, subject)
        if not member or member['role'] not in {'admin', 'researcher'}:
            raise ServiceError('没有修改该资料的权限', code='source_permission_denied', retryable=False)
        if policy and policy['collection_id'] and (not member or member['role'] != 'admin') and policy['owner_subject'] != subject:
            grant = await db.one("SELECT subject FROM collection_grants WHERE collection_id=? AND subject=? AND permission='write'",
                                 (policy['collection_id'], subject))
            if not grant:
                raise ServiceError('没有修改该资料的权限', code='source_permission_denied', retryable=False)


async def register_owner(db, workspace, document_id):
    await db.execute("INSERT OR IGNORE INTO document_policies(document_id,workspace_id,owner_subject) VALUES(?,?,?)",
                     (document_id, workspace, subject_for(workspace)))


async def require_sources(db, workspace, sources):
    """Check current ACL only; historical content/version changes do not rewrite a snapshot."""
    if not isinstance(sources, list):
        raise ServiceError('引用权限信息无效', code='source_permission_denied', retryable=False)
    for source in sources:
        if not isinstance(source, dict):
            raise ServiceError('引用权限信息无效', code='source_permission_denied', retryable=False)
        if source.get('kind') == 'local':
            await require_document(db, workspace, source.get('document_id', ''))
