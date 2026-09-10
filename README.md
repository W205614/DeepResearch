# DeepResearch 企业研究工作台

面向中文行业研究的企业单机演示环境。默认 Docker 部署以 PostgreSQL、Redis Worker、Milvus、Keycloak OIDC、ClamAV、OpenTelemetry、Prometheus、Grafana、Tempo 和 Loki 组成一条受权限约束、可恢复、可审计的研究链路；它用于复现企业能力，不等同于多副本、高可用生产集群。

系统以 LangGraph 编排多个具备独立职责、工具权限和结构化交接物的研究 Agent，并以来源约束与 SSRF 防护降低不可核查结论风险。默认企业单机演示环境使用 PostgreSQL、Redis Worker、Milvus、Keycloak OIDC、MinIO 对象存储、ClamAV、OpenTelemetry、Prometheus/Grafana、Tempo/Loki，提供工作空间权限、审计、隔离扫描、可恢复任务、数据导出与备份恢复能力。

## 企业能力清单

| 领域 | 当前企业单机演示能力 | 验证入口 |
| --- | --- | --- |
| 身份与权限 | Keycloak OIDC 授权码 + PKCE；后端只校验 JWT；工作空间 `admin / researcher / viewer` 角色和资源级服务端校验 | `scripts/smoke-enterprise.py`、Playwright E2E |
| 租户数据与审计 | 会话、研究、资料、检索、导出和死信恢复均按工作空间归属校验；关键管理动作写入不含正文的审计日志 | 权限单测、工作台设置页 |
| 研究执行 | 同进程职责受限的 LangGraph Agent 协作、SSE 进度、来源约束、引用核验、SSRF 防护和会话上下文边界 | 后端回归、冻结评测 |
| 资料安全 | MinIO 隔离区、ClamAV 扫描、扫描失败拒绝、异步索引、仅 `ready` 资料可检索 | 文档安全测试、页面状态 |
| 异步可恢复性 | Redis ARQ Worker、去重、检查点、重试、取消、心跳中断恢复、PostgreSQL 死信表与管理员恢复入口 | 故障演练、死信回归 |
| 数据服务 | PostgreSQL 为事务源、Milvus 向量检索、BM25 + 向量融合、MinIO 原件保存 | 检索冻结集与恢复脚本 |
| 可观测性 | Prometheus 指标、Grafana 面板、OTel Trace → Tempo、脱敏 JSON 应用日志 → Loki、Alertmanager → 可选飞书机器人 | 企业冒烟、Grafana Explore |
| 质量门禁 | Python 单测/静态检查、Vue 单测/生产构建、真实 OIDC 浏览器 E2E、Compose 冒烟、离线流程评测 | GitHub Actions `CI` |
| 迁移与恢复 | 幂等旧 SQLite 迁移、逻辑 SQL + 命名卷备份、恢复后冒烟验证 | `migrate_legacy_sqlite.py`、备份/恢复脚本 |

下文给出每项能力的边界和操作方式。这里的“企业”指架构与治理链路的单机复现，不代表多副本生产集群或 SLA。

## 多 Agent 协作边界

这不是把同一条提示词改成多个名称：协调器和八类专业 Agent 在同一进程、共享当前配置的模型端点运行，但每个 Agent 进入受限工具作用域，且把输入输出通过 `ResearchState` 和 `agent_handoff` 事件交接。它不是多个独立模型服务的分布式群体，部署成本和调度复杂度也因此较低。

| Agent | 允许工具 | 交接物 |
| --- | --- | --- |
| 协调路由 | 结构化模型、用户档案 | 路由模式或本地记忆动作 |
| 研究规划 | 结构化模型 | 问题、范围、差异化查询 |
| 网络调研 | 网页搜索、安全正文读取 | 网络证据 |
| 本地资料 | 用户资料混合检索 | 带位置的本地证据 |
| 证据裁决 | 结构化模型 | 接受来源、冲突、局限 |
| 分析与补搜 | 结构化模型 | 带来源的结论、缺口、补充查询 |
| 报告撰写 | 结构化模型 | 结构化草稿 |
| 引用核验 | 结构化模型 | 支撑判定与修订后的报告 |

执行过程通过 SSE 展示 Agent 启动、允许工具和交接记录；权限测试会拒绝规划 Agent 直接联网、网络调研 Agent 直接调用模型等越权调用。

## 企业单机演示启动

1. 复制 `.env.example` 为 `.env`，填写模型密钥，以及 `POSTGRES_PASSWORD`、`KEYCLOAK_DB_PASSWORD`、`KEYCLOAK_ADMIN_PASSWORD`、`MINIO_ROOT_PASSWORD` 和 `GRAFANA_ADMIN_PASSWORD`。如需飞书告警，另外填写可选的 `FEISHU_WEBHOOK_URL`。这些值不得提交。
2. 启动 Docker Desktop 后运行：

   ```powershell
   .\scripts\start.ps1
   ```

   或：

   ```powershell
   docker compose up -d --build --wait
   ```

3. 打开 `http://localhost:8080`，通过内置 Keycloak 登录；Grafana 位于 `http://localhost:3000`。

所有服务仅在 Docker 网络内互通，Web、Keycloak 与 Grafana 仅绑定本机回环地址。停止服务使用 `docker compose down`；不要使用 `down -v`，否则会删除演示数据卷。旧的 `compose.enterprise.yaml`、`compose.demo.yaml` 与 `compose.dev.yaml` 仅为兼容旧命令保留，不再改变运行拓扑。
## API 配置

最小配置如下：

```dotenv
LLM_MODEL_ID=deepseek-v4-flash
# 图片资料解析复用同一 LLM_BASE_URL 和 LLM_API_KEY
VISION_MODEL_ID=deepseek-v4-flash-vision-exp
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=你的 DeepSeek API Key

EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_BASE_URL=https://你的嵌入服务/v1
EMBEDDING_API_KEY=你的嵌入模型密钥
EMBEDDING_DIMENSION=3072
WEB_SEARCH_PROVIDER=deepseek
```

`deepseek` 使用 DeepSeek Responses API 的 `web_search` 工具，因此联网搜索不必只能使用博查。它只接受服务端返回的原生来源 URL，不把模型正文中的 URL 当作证据；若网页正文无法安全抓取，该 DeepSeek 结果不会进入报告。这样可避免将综合搜索回答错误归因给单一链接。

如需使用博查，将 `WEB_SEARCH_PROVIDER=bocha` 并设置 `BOCHA_API_KEY`。`auto` 会优先使用 DeepSeek，失败时才回退到博查。

运行配置检查：

```powershell
.\.venv\Scripts\python.exe .\scripts\check_apis.py --web-search
```

它只输出连接状态、嵌入维度和可验证来源数，不回显密钥、响应正文或来源 URL。

## 开发环境

Python 依赖安装在项目内 `.venv`，不会写入系统 Python：

```powershell
.\scripts\setup-dev.ps1
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.cache\pytest
.\.venv\Scripts\ruff.exe check backend tests scripts main.py
```

前端位于 `frontend`，生产镜像会在构建时执行类型检查与 Vite 打包。


## 检索评测与观测

`eval/frozen_cases.json` 只验证研究流程、路由和来源数量目标，`source_target_rate` 不是检索召回率。仓库包含一套冻结的内部回归语料：14 份资料、16 个问题，并为每个问题人工标注 `relevant_documents`。运行以下命令可在同一索引上对照单向量基线和当前 BM25 + 向量融合：

```powershell
# 真实嵌入只在 Docker 私有网络中执行；使用临时 SQLite 状态和自动清理的 dr_eval_* Milvus 集合
$workspace = (Get-Location).Path
docker compose run --rm --no-deps -v "${workspace}:/workspace:ro" backend python /workspace/scripts/evaluate_retrieval.py --corpus /workspace/eval/local_retrieval_corpus.json --cases /workspace/eval/local_retrieval_cases.json --output /tmp/retrieval-result.json
```

最新真实嵌入测量与边界见 [eval/benchmark_results.md](eval/benchmark_results.md)。输出包含文档级 Recall、Precision、nDCG、MRR、冷/热查询时延和嵌入请求成本代理。它衡量资料检索，不代表最终答案正确率；答案质量仍需单独标注证据支撑、完整性和正确性。研究任务的 SSE 事件还会写入 `local_retrieval`，记录缓存、BM25、query embedding、向量搜索、融合和总耗时。当前界面并不流式返回模型 token，因此不能把这些数据称为模型 TTFT。

### 可核验的作品集数据

- 在 14 份冻结资料、16 个人工标注中文查询与真实 `text-embedding-3-large` 服务上，BM25 + 向量融合的 Recall@5 为 **1.0000**，单向量对照为 **0.9688**；nDCG@5 为 **0.9676**，对照为 **0.9446**。
- 同一测试中，混合检索平均冷查询为 **1257.78 ms**；缓存命中后的平均热查询为 **94.87 ms**，热查询缓存命中率为 **100%**。

以上为 2026-09-07 的项目自有检索回归结果，适合写成“固定语料检索评测”；不能写成通用数据集成绩、回答准确率或生产 SLA。

## 后端目录

后端按职责拆分，避免将接口、研究图、存储和第三方适配器平铺在同一个目录：

```text
backend/
├── api/              # FastAPI 路由、SSE 和应用入口
├── commands/         # CLI 命令
├── core/             # 配置、认证、数据库、观测与网络安全
├── domain/           # 请求、响应和工作流数据模型
├── infrastructure/   # 模型搜索提供方、Milvus 与演示数据
├── research/         # 意图路由、证据规则和 LangGraph 编排
└── services/         # 研究运行服务与本地文档服务
```

## CLI

Docker 服务启动后，可使用有效的 Keycloak/OIDC access token 从 CLI 发起研究并在终端输出 Markdown 报告：

```powershell
docker compose exec -T backend python -m backend.commands.cli --base-url http://web --token "<OIDC access token>" research "比较企业知识库 Agent 的私有化与 SaaS 部署" --mode deep
```

增加 `--json` 可输出完整运行记录。CLI 调用同一套本地 API、事件、证据校验和数据卷，不会绕开 Web 工作台的安全规则。

## 研究边界

报告中的每条正文结论必须关联已接受的来源，并经过一次独立的模型辅助支持校验；不通过的结论会删除或修订。该机制降低无引用结论的风险，但不替代重要决策所需的人工原文核查。网页抓取还会验证协议、DNS 地址、跳转和响应大小，以阻断本机与内网地址访问。

除用户在界面手动选择“快速问答”或“深度研究”外，路由模型会结合当前 Thread 上下文判断回答是否需要网页、本地资料库或最新可核查外部事实。普通交流、对当前会话的追问和用户档案查询会走聊天或本地记忆动作，不进入研究检索；单点外部事实使用快速研究，多维或对比主题使用深度研究。同一 Thread 是一条连续研究会话：页面按时间连续显示提问与报告；后端注入最多 `CONVERSATION_TURN_LIMIT` 个历史问题和 `CONVERSATION_RECENT_RUNS` 份最近报告，避免长会话无限占用上下文。这两个值默认是 30 和 6，可在 `.env` 调整。会话历史、用户设定及历史研究摘要只用于理解上下文，不能作为本次研究事实来源。每次证据审查会记录来源数量、网页域名数、来源类型和正文/摘要状态，便于后续测量覆盖度与来源多样性。

例如，用户用自然语言为助手命名、询问助手名称或已保存偏好时，语义路由会返回对应的本地记忆动作；“上一个问题是什么”会只读取当前 Thread 的上一条问题。它们不会进入网页或资料库检索。跨 Thread 的助手名称和偏好按 User ID 保存；会话问题和研究摘要始终按 Thread 隔离。“AI 行业研究偏好”等需要外部事实的主题仍会进入研究流程。

重要的数字、日期、比例、金额和法规结论必须有正文或本地资料支持，并需要两个独立网页域名，或一个在 `SOURCE_TRUST_OVERRIDES` 中明确配置的可信域名。搜索摘要可以用于背景，但不能单独支撑这类结论。网页正文会安全缓存 `WEB_CACHE_TTL_HOURS` 指定的时长；缓存和重试不会绕过 DNS、跳转和内网地址限制。

深度研究默认每条查询取得最多 8 个结果，在全部查询间轮转选择最多 24 个不同网页候选，再读取可访问正文和筛选证据。可用 `.env` 中的 `WEB_RESULTS_PER_QUERY`、`MAX_WEB_CANDIDATES` 调整覆盖范围；提高它们会增加时延、模型上下文和搜索调用成本。工作空间只限制搜索次数和并发研究数；Token 使用会被记录用于成本观测，但不会作为“今日 Token 预算”拒绝用户请求。它是多源抽样与可核查综合，不是对整个互联网的穷尽抓取。

运行任务时，后端会向容器标准输出记录任务短 ID、节点开始/结束与耗时、模型调用、搜索结果数量、候选/可读网页数量、证据筛选数量和错误类别。查看命令为 `docker compose logs -f backend`。日志默认不记录问题文本、用户偏好、提示词、模型输入输出、URL 或网页正文；用 `TASK_LOG_LEVEL=WARNING` 可减少正常进度日志。

本地资料支持 TXT、Markdown、DOCX、文字型 PDF，以及 JPG/JPEG、PNG、GIF、WebP 图片。文本资料按标题、页码与 DOCX 表格结构切块；图片会先校验真实文件签名，再由 `VISION_MODEL_ID` 指定的 DeepSeek 视觉模型提取可检索文字、表格标题和图表事实，之后进入同一向量与 BM25 混合检索。扫描 PDF 仅在其嵌入图像可被安全提取时尝试视觉解析；PPT、Excel、复杂公式和通用版面理解仍不在当前范围。资料库页面可以查看 Chunk 定位、手动重建索引，并通过检索调试工作台查看向量、BM25 与融合分数。

默认不会自动把报告写入语义记忆。完成报告后可在界面点击“保存为语义记忆”，或设置 `AUTO_SAVE_SEMANTIC_MEMORY=true` 恢复自动保存。用户偏好和个人设定按 OIDC 账号私有，即使成员进入同一工作空间也不会互相读取或修改；报告语义记忆属于工作空间，但只会在保存它的 Thread 内被检索。工作台设置页可导出当前工作空间数据与本人个人记忆，或在确认后清理对应数据。

左侧“最近研究”中每个会话都可删除。删除会停止该会话仍在运行的任务，并一并清除该会话的报告、事件、计数和检查点；本地资料、用户偏好和个人设定不会受影响。已单独保存的报告语义记忆仍会保留在数据导出中，但由于原 Thread 已删除，不会再被注入后续研究。

默认 Compose 启动本机 Keycloak。打开工作台后，已有账号可登录，新用户可注册；右上角“退出”会结束浏览器和 Keycloak 会话，因此可以切换账号。每个账号的新会话自动使用 `thread01`、`thread02` 等短 Thread ID；内部 UUID 仅用于数据库关联与检查点，不会显示在工作台。用户 API 统一使用 OIDC JWT，不再保留与浏览器认证冲突的第二套工作台访问令牌。认证流程详见 [docs/local-keycloak-login.md](docs/local-keycloak-login.md)。

运行无需外部 API 的评测基线：

```powershell
.\.venv\Scripts\python.exe .\scripts\evaluate.py
```

它在固定资料上输出路由正确率、来源数、引用检查、时延与 Token 基线到 `.cache/eval/`，不代表真实行业研究质量。
### 最终回答质量门禁

回答门禁包含 7 个生成题和 3 个固定错误反例，结构检查之外另行逐条判定引用是否支持否定、数字主体、日期和范围；判定缺失、重复或模型失败均不通过。不能再把关键词出现当作事实正确。

新增 `eval/research_quality_cases.json` 的 30 题固定证据完整研究评测：实际执行研究图，图外评分，关键题全部通过、整体通过率至少 85%、引用支持率至少 95%、应回答问题覆盖率至少 85%。最终本机复验 30/30 通过、引用支持率 100%、覆盖率 100%；此前 29/30 及 CI 失败记录保留。另有 3 题实时联网浏览器验收通过。这些是小规模合成题集与公开主题验收，不是通用准确率。
```powershell
# 要求 .env 中已有 LLM_API_KEY；缺失密钥会失败，而不是跳过。
.\.venv\Scripts\python.exe .\scripts\evaluate_answers.py
```

GitHub Actions 的 **Answer Quality Gate** 从仓库 Secret 读取 `LLM_API_KEY`、可选 `LLM_MODEL_ID` 和 `LLM_BASE_URL`。该工作流还执行完整 30 题评测。在分支保护中将该检查设为 Required 后，未配置密钥或质量退化都会阻断合并。

## 企业单机演示环境

企业单机演示环境使用默认 Compose 启动：

```powershell
docker compose up -d --build --wait
```

默认编排直接包含 PostgreSQL、Redis Worker、OpenTelemetry Collector、Prometheus、Grafana、Tempo、Loki、Keycloak 与 MinIO；不再存在功能较低的本地运行拓扑。后端只接受 OIDC JWT；浏览器提供的 `X-User-ID` 不作为生产身份。工作空间成员角色为 admin、researcher、viewer，审计日志不记录研究正文、来源地址或密钥。

当前 Compose 适合单机演示；运行、备份和恢复说明见 [docs/operations.md](docs/operations.md)。
使用下面的命令执行不涉及模型调用的集成冒烟检查。它会验证 Web、Keycloak OIDC 发现、PostgreSQL 迁移版本、Redis、Worker、后端 `/readyz` 和 OpenTelemetry Collector 的连通性：

```powershell
.\scripts\smoke-enterprise.ps1
```

首次启用企业单机演示环境时，Keycloak 的 `keycloak-data` 命名卷会保存本机注册账号和管理台改动；不要用 `docker compose down -v` 停止演示环境。
Grafana 仅映射到 `http://localhost:3000`，使用 `.env` 的 `GRAFANA_ADMIN_PASSWORD` 登录；Prometheus 数据源会在启动时自动连接到容器内的 Prometheus。Alertmanager 也只映射到本机的 `http://localhost:9093`。

## 企业演示验证

企业单机演示环境使用 Keycloak、PostgreSQL、Redis Worker、Milvus、Prometheus、Grafana 与 ClamAV。启动后打开 `http://localhost:8080` 登录；监控面板在 `http://localhost:3000` 的 **Dashboards → DeepResearch**。Grafana 仅监听本机。

```powershell
.\scripts\smoke-enterprise.ps1
```

资料上传在企业单机演示环境会先经 ClamAV 扫描；扫描服务不可用时上传会被拒绝。连续会话中的每条历史研究都有“查看来源 N”入口，可切换并查看该次研究保存的引用与本地资料来源。

`migrate` 是一次性 Alembic 迁移任务，显示 `Exited (0)` 表示成功完成，应保留；不要执行 `docker compose down -v`，否则会删除本机演示数据卷。

## 企业演示强化说明

企业单机演示环境将原始资料先写入 MinIO 的 `quarantine/<document-id>` 对象键，再交由 ClamAV 扫描；通过后原子移动至 `uploads/<document-id>`。只有 `ready` 状态的资料可检索、用于研究或导出：`scanning` 表示正在扫描，`indexing` 表示已通过扫描且正在建立索引，`quarantined` 表示扫描拒绝，`scan_failed` 表示扫描服务不可用。后两种状态保留最少的文件记录和错误说明，不能通过重建索引绕过扫描；管理员删除后会同时删除对应对象，并写入不含正文的审计记录。

研究与资料索引均有持久化状态。企业单机演示环境研究任务由 Redis Worker 消费，每次尝试使用固定的队列任务编号；重复恢复不会并行执行同一研究。点击停止会先将研究持久化为 `cancelled`，再向 Worker 发送中止信号，因此排队任务和已开始的任务都会停止，晚到的 Worker 也不能重新领取它。Worker 每 10 秒更新一次心跳；只有连续 45 秒未更新的运行中任务才会被标记为中断并从检查点重新入队。资料索引在 API 或 Worker 重启后会重新入队，已通过扫描的原始文件不会因进程内任务丢失而被视为可检索。任务、指标和追踪不使用用户、工作空间、主题、文件名、来源 URL 或错误正文作为 Prometheus 标签或 OpenTelemetry 属性。

Grafana 自动配置六个全局匿名面板：API/Worker 可用性、队列深度、任务成功率、失败类别、节点 P95 时延和 Token 消耗。Worker 启动时会预先暴露 `completed`、`insufficient`、`failed` 三类终态计数器的零值，使 Prometheus 在首个任务前取得基线。任务成功率只计算最近 5 分钟内有终态任务的窗口；`completed` 与因证据不足而安全结束的 `insufficient` 均计为成功。无样本时显示 `N/A`，不再误显示为 0% 或 `NaN`。刚重建容器后，等待一个 15 秒抓取周期再运行研究，即可在下一个抓取周期看到成功率。

企业 Compose 冒烟在本机和 GitHub Actions 复用标准库脚本：

```powershell
.\scripts\smoke-enterprise.ps1 -Start
```

它不会调用模型或联网搜索，检查 Web、OIDC、迁移、Redis、`/readyz`、Worker 指标、Prometheus 两个抓取目标、Grafana 与 OTel Collector。

备份脚本会先拒绝存在运行中研究或索引任务的环境，再短暂停止服务形成一致冷备。它同时导出业务库与 Keycloak 库的 PostgreSQL 自定义格式逻辑备份，以及保存资料原件的 MinIO、Milvus、Redis、Keycloak、Grafana 等命名卷，并生成 SHA-256 清单：

```powershell
.\scripts\backup.ps1
.\scripts\restore.ps1 -BackupPath .cache\backup\deepresearch-时间戳 -Project dr-restore-check
# 安装本机每日 02:00 备份与最近 7 份保留（首次需手动执行）
.\scripts\install-backup-task.ps1
```

恢复只写入全新的 `dr-restore-*` Compose 项目和临时卷，不替换当前项目数据。它校验归档文件、恢复两个 SQL 备份，并检查就绪状态、匿名访问拒绝、OIDC 发现及 Milvus 检索；结束后清理临时恢复卷。不要将备份文件、`.env` 或密钥提交到 Git。

前端工具链依赖通过 Dependabot 分组升级，并以 `npm ci`、Vitest 和生产构建作为合并门槛。当前 Vue TSC 3.3.11 与 TypeScript 7.0.2 实际不兼容，因此项目固定 TypeScript 6.0.3，并暂时忽略 TypeScript 7 的自动升级；只有完成兼容性验证后才解除该限制。

## 死信、追踪与告警

最终失败的研究任务会进入 PostgreSQL 死信表；管理员可在网页的“工作台设置 → 死信任务治理”查看不含研究正文的失败摘要，并从检查点恢复。API 仍提供 GET /api/workspaces/{workspace_id}/dead-letters 和 POST /api/workspaces/{workspace_id}/dead-letters/{run_id}/recover，恢复动作写入审计日志。运行日志为 JSON，包含稳定错误类别、任务短 ID 与 OpenTelemetry Trace ID，但不写入研究正文、URL 或密钥。Prometheus 内置队列积压、死信和失败率三条告警规则，Prometheus 会发送到 Compose 内部的 Alertmanager，再由后端内部入口转换为飞书群机器人文本消息。Compose 首次启动会在独立命名卷生成内部 Bearer token，Alertmanager 和后端只读挂载；缺失或错误令牌的转发请求会被拒绝。应用层对同一告警状态去重、对短暂 Webhook 错误最多重试两次，并保留已恢复事件；`deepresearch_alert_deliveries_total{result="succeeded|failed|disabled"}` 与 `deepresearch_alert_suppressed_total` 可审计投递与去重结果。`.env` 中可选的 `FEISHU_WEBHOOK_URL` 留空即禁用外发；它只应写入被忽略的本机 `.env`，不要放入 README、截图、提交或工单。Alertmanager 的验证可向 `http://localhost:9093/api/v2/alerts` 提交一条临时告警，随后检查飞书群是否收到“DeepResearch Alert”消息。

## 持久化日志、追踪与浏览器回归

默认 Compose 将后端和 Worker 已有的脱敏 JSON 任务日志通过 OpenTelemetry Collector 写入 Loki，将 FastAPI 请求和 Worker 任务 Span 写入 Tempo；Grafana 自动配置 `Prometheus`、`Loki`、`Tempo` 三个内部数据源。Tempo 与 Loki 不映射宿主机端口；日常通过 Grafana 的 Explore 查询。日志链路不挂载 Docker Socket，不采集其他容器、浏览器流量、资料正文、提示词、来源 URL 或密钥。

在 Grafana 的 **Explore** 中选择 Loki，可使用：

```logql
{service_name=~"deepresearch-api|deepresearch-worker"}
```

选择 Tempo 后按服务 `deepresearch-api` 或 `deepresearch-worker` 查询 Trace。任务日志中的 `trace_id` 可跳转到 Tempo；Tempo 也会根据服务名回查 Loki。Tempo 的本地文件块和 Loki 的本地 TSDB 是为了单机演示保留，生产多副本必须改为共享对象存储、鉴权代理、保留期与容量策略。

真实浏览器回归不使用开发身份头，也不需要模型调用。启动 Compose 后运行：

```powershell
Set-Location frontend
npm run test:e2e:enterprise
```

脚本只从被忽略的本机 `.env` 读取 `KEYCLOAK_ADMIN_PASSWORD`（或读取同名环境变量），临时创建随机 Keycloak 用户，验证匿名 API 返回 `401`、Keycloak 授权码 + PKCE 登录、受保护工作台和设置页，随后删除临时身份和成员关系；仅清理脚本创建的临时身份及其工作空间，不清理日常演示数据。失败时本机保留 `frontend/test-results/` 中的截图、视频与 Trace 供排查；该目录被忽略，CI 不会上传这些产物。

## 重建、验证与恢复操作手册

日常重建与最小验收：

```powershell
docker compose up -d --build --wait
.\scripts\smoke-enterprise.ps1
Set-Location frontend
npm ci
npm run test:e2e:enterprise
```

企业冒烟会验证 Web、OIDC 发现、迁移、Redis、后端就绪、Worker 指标、Prometheus 抓取目标、Grafana、OTel Collector、Tempo Trace，以及经受控脱敏探针确认可写入的 Loki 日志。E2E 额外验证真实浏览器认证边界。首次安装 Playwright 时运行 `npx playwright install chromium`；GitHub Actions 会安装 Chromium 后执行同一 E2E。

故障与数据恢复按下面顺序进行：

1. 任务或资料异常：在“工作台设置 → 死信任务治理”查看失败类别；管理员从检查点恢复，恢复动作会写入审计日志。
2. 依赖短暂失败：运行 `.\scripts\drill-dependency-recovery.ps1`，它会短暂停止 Milvus、确认就绪检查拒绝流量、恢复依赖与 Worker，再执行企业冒烟。
3. 数据备份与恢复：确认没有运行中任务后运行 `.\scripts\backup.ps1`；用 `.\scripts\restore.ps1 -BackupPath .cache\backup\deepresearch-时间戳 -Project dr-restore-check` 在全新隔离卷执行恢复烟测。成功报告写入备份目录的 `verification.json`，源数据卷始终保持不变。
4. 观测排查：先看 Grafana 的队列深度、近 5 分钟成功率、失败类别和 P95；再在 Loki 以服务名过滤日志，最后按 Trace ID 在 Tempo 定位节点链路。无终态任务的成功率显示 `N/A` 是预期行为，不是失败。

停止环境使用 `docker compose down`。不要使用 `docker compose down -v`，除非明确要删除所有本机演示数据卷。

## 当前边界（本次不继续扩展）

已完成的是企业单机演示基线。以下事项保留为生产化讨论边界，不属于本次面试项目继续优化清单：

- 本机即可完成：PostgreSQL RLS 策略与数据库角色、反向代理 TLS/本地 CA、密钥托管替代 `.env`、备份定时与恢复演练记录、Trace/日志保留策略和更细粒度的浏览器 E2E 场景。
- 需要真实基础设施才能证明：多副本 API/Worker、共享对象存储、高可用 PostgreSQL/Redis/Milvus、容量压测、跨机故障转移、外部 KMS/Vault、集中身份目录（SAML/SCIM）和真实值班升级体系。
- 需要组织流程才能证明：告警分级、飞书/钉钉/企业微信的轮值人员路由、RPO/RTO 承诺、变更审批、数据分级与合规审计。

因此简历或面试应表述为“完成企业单机演示基线及可恢复、可观测、权限闭环验证”，不要表述为“已上线高可用生产集群”或“达到 SLA”。
## 压测与故障演练

`load-health.js` 只用于连通性；业务读链路使用临时 OIDC 身份运行 `load-authenticated-read.js`。验证脚本会创建临时客户端和用户、获取令牌、执行压测并清理测试身份：

```powershell
# 默认 10 VU、5 分钟：成功率至少 99%、错误率低于 1%、P95 不高于 2 秒。
.\.venv\Scripts\python.exe .\scripts\verify-authenticated-load.py
# 快速本机回归示例；默认仍为 10 VU、5 分钟
.\.venv\Scripts\python.exe .\scripts\verify-authenticated-load.py --vus 10 --duration 1m
```

这只是本机、固定数据和读接口的容量基线，不是 SLA。`scripts/drill-dependency-recovery.ps1` 会短暂停止 Milvus；`scripts/drill-worker-restart.ps1` 会重启 Worker 并执行企业冒烟。使用本地告警接收器演练时，运行 `docker compose -f compose.yaml -f compose.test.yaml up -d --build --wait` 后执行 `python scripts/drill-alert-relay.py`；它不会向真实飞书群发消息。死信表会按 timeout、network、source_blocked、provider_configuration、model_response 等稳定类别记录最终失败。


## 面试项目收尾验收（2026-09-10）

新增迁移 `0004_consistency` 保留已有数据。资料版本阻止删除后迟到索引复活；请求幂等先于新任务额度校验；UTC 日搜索台账与单任务额度原子预占，失败调用及备用搜索分别计数；Worker 尝试版本约束心跳与终态，持久化任务对账补偿 Redis 投递失败。

- Python 108 通过，真实组件 4 通过，前端 4 通过及构建通过。
- 隔离 PostgreSQL、Redis、Milvus、MinIO、ClamAV；5 客户端持续 5 分钟，4560 请求无非预期失败，379 个接受任务全部完成。控制/读取 P95 243.71 ms，研究、排队与索引耗时单独记录。模型和搜索为可控替身，不代表真实模型吞吐。
- Worker 强制终止后 52.44 秒恢复，Redis 投递与 Milvus 就绪恢复通过；11 卷隔离备份恢复耗时 202.05 秒，临时资源已清理。
- 日常 Docker 重建、企业冒烟、真实 OIDC 浏览器、资料引用、SSE 重放、取消恢复和角色边界已验收。

```powershell
python scripts/verify_components.py --load --drill
.\.venv\Scripts\python.exe scripts/evaluate_research.py
```

[交付记录与完整复现命令](docs/interview-delivery.md) · [面试故障证据与设计取舍](docs/interview-evidence.md) · [脱敏结果](eval/delivery-results.json)。达到本次阈值后停止增加功能；GitHub Actions 状态以对应提交为准。
