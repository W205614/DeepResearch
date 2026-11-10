# CodeQL 单项核验：2026-11-10

核验基线为 `0005df53b742cdc2b79ef9196208cf0d00f8a0b8` 的实际生产源码与同提交 CodeQL 告警。本文件记录精确匹配项的处置依据，不按目录、语言或漏洞类别批量豁免。生产 Java 源码及原始 SQLite 网页未改写。

## 告警 1、2：`java/ssrf`

位置：`business-backend/src/main/java/com/deepresearch/business/service/AgentClient.java`，95、144 行。该文件 SHA-256 为 `9729af95b2760eb00fdc36f8e86f8abaa7f0f4deb999f976a9342d7a93365aea`；实际工作区字节已与该提交 Git blob 比较一致。

**结论：在当前固定 Agent origin、Servlet 请求路径和不跟随重定向的实现下为 false positive。**

- `uri()` 将运维配置的 `agentBaseUrl` 放在 URI 的最前面，再追加 Servlet 的 request URI 与 query。外部请求控制路径和查询值，不能选择 Agent 的 scheme、host 或 port；未使用 `URI.resolve()` 将 `//other-host` 当成新 authority。
- 代理 Controller 只映射已列明的 `/api/...` 路径；`get()` 的健康调用路径为常量 `/readyz`，Outbox 的 `internal()` 调用只构造内部任务路径。
- `HttpClient` 未启用 `followRedirects()`。[JDK 21 官方约定](https://docs.oracle.com/en/java/javase/21/docs/api/java.net.http/java/net/http/HttpClient.Builder.html#followRedirects(java.net.http.HttpClient.Redirect))的默认重定向策略是 `NEVER`。
- 新增的 `AgentClientOriginTest` 直接运行真实 `AgentClient` 和独立、临时的 loopback HTTP 端点，没有 mock URI 构造、HTTP 请求或网络结果。包含 URL/authority 语法的 path、编码斜杠和 query 都只访问配置的 Agent；两个 `forward()` 重载及 `get()` 遇到跨 origin 302 均返回 302，另一个 origin 的实际请求计数为 0，转发 Bearer header 未泄露。
- 以 `//` 开头的 path 单独使用 Agent 端口上的临时 socket 捕获真实 HTTP 请求。测试逐字验证 request-target、配置端口对应的 `Host`、Bearer header 和 body，要求该端口返回严格的 `200 agent-only`；其他 origin 的请求计数为 0。这保留了 authority-looking path 的完整攻击用例，不依赖测试服务器对这种路径的解析策略。

证明测试：`business-backend/src/test/java/com/deepresearch/business/service/AgentClientOriginTest.java` 中的 `pathAndQueryCannotReplaceTheConfiguredOrigin`、`authorityLookingPathIsSentToTheConfiguredAgentSocket`、`queryAuthoritySyntaxRemainsQueryDataForTheStreamingProxy`、`redirectsNeverForwardRequestsOrCredentialsToAnotherOrigin`，4/4 通过。

原先将全部四种路径都交给 `HttpServer` 并要求 200 的测试在 Windows JDK 21.0.1 通过，但在 CI Temurin 21.0.13 失败。定向隔离复现确认：仅前导 `//` 路径被新版本 `HttpServer` 在 handler 之前拒绝，返回 400 及 `Bad request URI`，其余三种路径均为 200。这是测试夹具的解析差异，不是代理选择了其他 origin。修正为上述逐字 socket 捕获后，Windows Maven 执行此类 4/4 通过；同一批实际编译的测试 class 在无外部网络的 Linux Temurin 21.0.13+9 容器内由 JUnit Platform 6.0.3 执行也为 4/4 通过。未放宽为任意响应状态，也未按平台版本分支或删除该输入。

本判断不允许将任意用户 URL 设为 `agentBaseUrl`，也不覆盖未来启用跨 origin 重定向或更改 URI 构造方式的代码。

## 告警 3：`java/spring-disabled-csrf-protection`

位置：`business-backend/src/main/java/com/deepresearch/business/config/SecurityConfig.java`，34 行。文件 SHA-256 为 `4dbaf829a97464e83d29329f41aa33888a0d26d4e28362c88b82f14e9d01afc7`；实际工作区字节已与基线 Git blob 比较一致。

**结论：在当前仅显式 Authorization Bearer header 认证、无 cookie/session 自动认证的资源服务器中为 false positive。** 依据不是“STATELESS 就天然没有 CSRF”：关键条件是浏览器不会自动补上此 API 所需的 Bearer header。

- 生产 `SecurityConfig` 使用 OAuth2 JWT resource server 和 `SessionCreationPolicy.STATELESS`，未配置 cookie、query 或 form 的 Bearer resolver，没有启用 form login 或 HTTP Basic。
- 前端 `frontend/src/api.ts` 的认证请求从 `sessionStorage` 获取 token，并显式写入 `Authorization` header，没有将其转成认证 cookie。[Spring Bearer token 文档](https://docs.spring.io/spring-security/reference/7.0/servlet/oauth2/resource-server/bearer-tokens.html)说明默认 resolver 的 Header 契约。
- `SecurityConfigBearerTest` 使用实际生产 `SecurityConfig`、`PlatformAccountFilter`、`ApiRateLimitFilter`，以临时 2048 位 RSA 密钥签发 JWT 并用真实 Nimbus decoder 验签。只隔离与此结论无关的数据库平台状态和 Redis 额度依赖。
- 带合法 JWT 的 cookie、查询参数、表单字段或预填已认证 SecurityContext 的 HttpSession，仍不能授权跨站 POST，实际写入次数为 0；无 CSRF token 的合法 Header 请求能够写入且没有创建认证 session/cookie；后续不带 Header 的请求仍为 401。非法 Header 不能借用合法 cookie token。

证明测试：`business-backend/src/test/java/com/deepresearch/business/config/SecurityConfigBearerTest.java`，4/4 通过。若以后引入自动附带的 cookie、session、Basic 凭据，应重新评估，不能继承本结论。

## 告警 133：`py/insecure-protocol`

位置：`tests/test_oidc_tls.py` 中的隔离 HTTPS 测试服务。

**真实修复：** 为 server `SSLContext` 显式设置 `minimum_version = ssl.TLSVersion.TLSv1_2`，不依赖当前 OpenSSL 的默认最低版本。保留真实临时 CA、证书和 loopback HTTPS 验证；生产认证逻辑没有改变。`pytest -q tests/test_oidc_tls.py` 为 3/3 通过。等待提交后的新 CodeQL 结果确认告警自动关闭，不将其标记为误报。

## 告警 143：`js/xss-through-dom`

位置：`deployment/security/agent-native-evidence/supplement-20261107/sqlite-release-3_54_1.html`，97 行。

**结论：对这个精确、静态的上游取证文档为 false positive；它不是应用提供给用户的网页。**

- 原始来源为 [SQLite 3.54.1 release log](https://www.sqlite.org/releaselog/3_54_1.html)，SHA-256 为 `69bf98f8cb8730c88b01414cdc4436fb3c65443dae405957b40efab9976f1e12`，与 `agent-native-fixed-code-supplement-20261107.json` 中的锁定值及基线 Git blob 一致。`check_agent_native_packages.py` 只读取其字节并校验摘要，没有将其作为模板执行或提供 HTML 路由。
- 该告警语句取 `id=mtimelink` 节点的 `data-href` 并赋给 `href`。对原始 HTML 用标准库 `HTMLParser` 枚举到的全部 id 为 `search_menubutton`、`submenu`、`searchmenu`、`searchtype`、`searchbox`，没有 `mtimelink`；该精确文档中条件赋值分支不会执行。
- `compose.yaml` 将 Web build context 限定为 `./frontend`；`frontend/Dockerfile` 在该 context 构建 Vite dist，运行镜像只复制 dist 和 Nginx 配置。此证据位于 context 之外，没有进入 frontend src/public 或 Nginx webroot。Python Dockerfile 只 COPY 运行模块和列明脚本，Java 使用 `./business-backend` context；没有为本文件提供应用静态文件挂载。

证明文件：原始 HTML、`deployment/security/agent-native-fixed-code-supplement-20261107.json`、`scripts/check_agent_native_packages.py`、`compose.yaml`、`frontend/Dockerfile`、`Dockerfile`。保留原文及告警原始扫描，未通过改写上游材料或排除扫描目录消除结果。

## 本地验证

```powershell
& 'E:/download/apache-maven-3.9.4/bin/mvn.cmd' -B -f business-backend/pom.xml '-Dtest=AgentClientOriginTest,SecurityConfigBearerTest' test
& .venv/Scripts/python.exe -B -m pytest -q tests/test_oidc_tls.py
& .venv/Scripts/python.exe -B -m ruff check tests/test_oidc_tls.py
git diff --check
```

结果：Java 8 passed、0 skipped；Python 3 passed；Ruff 和差异空白检查通过。另将 Maven 实际编译的 Origin 测试 class 在 Linux Temurin 21.0.13+9 隔离容器中执行，4 passed、0 skipped。未启动日常栈、访问业务数据库、调用供应商 API 或更新公开镜像。本地结论不替代父任务提交后对实际 CI、SARIF 与逐项告警状态的核验。
