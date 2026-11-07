"""Current platform account state shared by HTTP requests and durable jobs."""
from .db import now
from .reliability import ServiceError


async def register_account(db, principal) -> None:
    stamp = now()
    await db.execute("""INSERT INTO platform_users(subject,status,revoked_before,display_name,created_at,updated_at)
        VALUES(?,'active',0,?,?,?) ON CONFLICT(subject) DO UPDATE SET
        display_name=excluded.display_name,updated_at=excluded.updated_at""",
        (principal.subject, principal.display_name, stamp, stamp))


async def require_active_subject(db, subject: str) -> None:
    row = await db.one("SELECT status FROM platform_users WHERE subject=?", (subject,))
    # Existing jobs can predate the account registry. Their real workspace
    # membership is still checked by Execution/UsageGovernance, never inferred.
    if row and row["status"] != "active":
        raise ServiceError("账号已停用，请联系平台管理员", code="account_disabled", retryable=False,
                           action="联系平台管理员")


async def require_current_account(db, principal) -> None:
    row = await db.one("SELECT status,revoked_before FROM platform_users WHERE subject=?", (principal.subject,))
    if not row:
        raise ServiceError("账号状态不可确认，请重新登录", code="account_unregistered", retryable=False,
                           action="重新登录")
    if row["status"] != "active":
        raise ServiceError("账号已停用，请联系平台管理员", code="account_disabled", retryable=False,
                           action="联系平台管理员")
    if row["revoked_before"] and principal.issued_at <= row["revoked_before"]:
        raise ServiceError("原登录会话已撤销，请重新登录", code="session_revoked", retryable=False,
                           action="重新登录")
