# 本机 Keycloak 登录与注册

打开 `http://localhost:8080`：已有账号选择“登录并开始研究”；新用户选择“创建本地账号”。页面会跳转到本机 Keycloak，注册完成后自动回到工作台。

工作台右上角的“退出”会清除当前浏览器会话和 Keycloak 会话，再回到登录页；此时可使用另一账号登录或注册新账号。

Keycloak 管理台位于 `http://localhost:8180/admin`。管理员可管理用户、禁用账号或重置密码；普通用户不需要进入管理台。
默认与企业版 Compose 都使用 `keycloak-data` Docker 卷保存注册用户和管理台改动。首次切换到这个版本前，如你正在运行旧版、且需要保留旧容器里临时创建的本地账号，请先在 Keycloak 管理台导出 realm，或重新注册测试账号；不要执行 `docker compose down -v`，它会清空所有命名卷。

前端仅在当前浏览器会话保存 Access Token，并通过 `Authorization: Bearer <token>` 调用 API。Token issuer 为 `http://localhost:8180/realms/deepresearch`，后端通过 Docker 内部的 `keycloak:8080` 读取 JWKS。
