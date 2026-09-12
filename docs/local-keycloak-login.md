# 本机 Keycloak 登录与注册

打开 `http://localhost:8080`：已有账号选择“登录并开始研究”；新用户选择“创建本地账号”。页面会跳转到本机 Keycloak，注册完成后自动回到工作台。

工作台右上角的“退出”会清除当前浏览器会话和 Keycloak 会话，再回到登录页；此时可使用另一账号登录或注册新账号。

Keycloak 管理台位于 `http://localhost:8180/admin`。管理员可管理用户、禁用账号或重置密码；普通用户不需要进入管理台。
默认与企业版 Compose 都使用 `keycloak-data` Docker 卷保存注册用户和管理台改动。首次切换到这个版本前，如你正在运行旧版、且需要保留旧容器里临时创建的本地账号，请先在 Keycloak 管理台导出 realm，或重新注册测试账号；不要执行 `docker compose down -v`，它会清空所有命名卷。

前端在当前标签页的 `sessionStorage` 保存 Access Token、ID Token 和 Refresh Token，并通过 `Authorization: Bearer <token>` 调用 API。Token issuer 为 `http://localhost:8180/realms/deepresearch`，后端通过 Docker 内部的 `keycloak:8080` 读取 JWKS。


请求前检查 Access Token 的到期时间，距过期不足 30 秒时用 Refresh Token 续期，并保存服务端返回的轮换令牌。并发请求共用一次续期；API、进度流重连和文件下载使用同一入口。请求收到 401 时尝试刷新并最多重发一次；403 不会触发退出登录。

网络故障或身份服务暂时不可用时保留凭证，便于后续重试；服务端明确返回 `invalid_grant` 或刷新后请求仍返回 401 时清理凭证并显示登录页。退出过程中完成的旧刷新结果不会恢复已退出的登录。任务在服务端独立执行，重新登录后会依据原会话标识恢复查看。

从没有保存 Refresh Token 的旧版升级时，需要刷新页面并重新登录一次。自动续期已有前端模拟测试覆盖；2026-09-12 的联网研究实测均未达到 300 秒令牌有效期，未用这些样本证明真实长任务续期成功。
