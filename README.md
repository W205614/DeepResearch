# DeepResearch 多 Agent 行业研究助手

面向中文行业研究的本地 Web 应用与企业化演示后端。系统以 LangGraph 编排路由、规划、联网检索、本地资料检索、证据审查、分析、反思、写作和引用校验等节点，并以来源约束与 SSRF 防护降低不可核查结论的风险。基础模式使用 SQLite 与 Milvus；企业演示模式切换至 PostgreSQL、Redis Worker 和 Keycloak OIDC，提供工作空间角色权限、审计日志、ClamAV 资料隔离、可恢复任务、OpenTelemetry、Prometheus/Grafana 观测，以及数据导出与备份恢复能力。

## 本地启动

1. 复制 `.env.example` 为 `.env`，填写自己的 API 配置。`.env` 已被 Git 忽略，不能提交。
2. 安装并启动 Docker Desktop。
3. 在项目根目录运行：

   ```powershell
   .\scripts\start.ps1
   ```

   或直接运行：

   ```powershell
   docker compose up -d --build --wait
   ```

4. 打开 `http://localhost:8080`。

仅 Web 服务绑定到 `127.0.0.1:8080`；后端、Milvus、etcd 和 MinIO 不暴露主机端口。数据保存在 Docker 命名卷中。

停止服务：

```powershell
docker compose down
```

开发模式允许暴露 Milvus 端口：

```powershell
docker compose -f compose.yaml -f compose.dev.yaml up -d --build
```

默认 Compose 使用本地 development 身份模式：页面会自动取得仅供本机使用的开发会话，不会跳转 Keycloak。需要账号注册、OIDC 和企业服务时，使用企业 Compose 覆盖层；不要混用默认后端与遗留 Keycloak 容器，否则 OIDC token 无法被 development 后端验证。

不配置外部 API 时可运行固定数据的演示模式。若主服务正在运行，先执行 `docker compose down` 释放 8080 端口：

```powershell
docker compose -f compose.yaml -f compose.demo.yaml up -d --build --wait
```

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


## 本地 RAG 评测与观测

`eval/frozen_cases.json` 只验证研究流程、路由和来源数量目标，`source_target_rate` 不是检索召回率。真实 RAG 质量使用带相关资料标注的独立语料：复制 `eval/local_retrieval_corpus.template.json` 与 `eval/local_retrieval_cases.template.json`，填入固定文件版本和每个问题的 `relevant_documents`，再运行：

```powershell
.\.venv\Scripts\python.exe .\scripts\evaluate_retrieval.py --corpus .\eval\local_retrieval_corpus.json --cases .\eval\local_retrieval_cases.json
```

输出包含文档级 Recall@3/5/10、nDCG@3/5/10、MRR，以及同一查询的冷缓存与热缓存检索耗时。它衡量资料检索，不代表最终答案正确率；答案质量仍需单独标注证据支撑、完整性和正确性。研究任务的 SSE 事件还会写入 `local_retrieval`，记录缓存、BM25、query embedding、向量搜索、融合和总耗时。当前界面并不流式返回模型 token，因此不能把这些数据称为模型 TTFT。

## 后端目录

后端按职责拆分，避免将接口、研究图、存储和第三方适配器平铺在同一个目录：

```text
backend/
├── api/              # FastAPI 路由、SSE 和应用入口
├── commands/         # CLI 命令
├── core/             # 配置、SQLite、日志与网络安全
├── domain/           # 请求、响应和工作流数据模型
├── infrastructure/   # 模型搜索提供方、Milvus 与演示数据
├── research/         # 意图路由、证据规则和 LangGraph 编排
└── services/         # 研究运行服务与本地文档服务
```

## CLI

Docker 服务启动后，可从容器内使用 CLI 发起研究并在终端输出 Markdown 报告：

```powershell
docker compose exec -T backend python -m backend.commands.cli --base-url http://web research "比较企业知识库 Agent 的私有化与 SaaS 部署" --mode deep
```

增加 `--json` 可输出完整运行记录。CLI 调用同一套本地 API、事件、证据校验和数据卷，不会绕开 Web 工作台的安全规则。

## 研究边界

报告中的每条正文结论必须关联已接受的来源，并经过一次独立的模型辅助支持校验；不通过的结论会删除或修订。该机制降低无引用结论的风险，但不替代重要决策所需的人工原文核查。网页抓取还会验证协议、DNS 地址、跳转和响应大小，以阻断本机与内网地址访问。

路由会优先采用可解释的规则处理用户指定模式、问候、功能帮助、助手名称和明确的研究信号。问候、功能帮助、个人设定与已保存偏好是仅限本地操作；除此之外，任何普通问题即使模型初判为聊天，也会进入快速研究，尝试同时检索网页与当前用户的本地资料库，避免直接使用模型参数知识回答。同一 Thread 是一条连续研究会话：页面按时间连续显示提问与报告；后端注入最多 `CONVERSATION_TURN_LIMIT` 个历史问题和 `CONVERSATION_RECENT_RUNS` 份最近报告，避免长会话无限占用上下文。这两个值默认是 30 和 6，可在 `.env` 调整。会话历史、用户设定及历史研究摘要只用于理解上下文，不能作为本次研究事实来源。每次证据审查会记录来源数量、网页域名数、来源类型和正文/摘要状态，便于后续测量覆盖度与来源多样性。

“当前用户的研究偏好是什么”“你保存了哪些研究偏好”等问题会直接读取当前 User ID 保存的偏好，不调用模型或联网搜索；“AI 行业研究偏好”等行业主题仍会进入研究流程。用户也可以说“以后叫你小研”设置助手名称；同一 User ID 下的任意新 Thread 问“你叫什么名字？”都会直接读取该个人设定，不联网、不调用模型。

重要的数字、日期、比例、金额和法规结论必须有正文或本地资料支持，并需要两个独立网页域名，或一个在 `SOURCE_TRUST_OVERRIDES` 中明确配置的可信域名。搜索摘要可以用于背景，但不能单独支撑这类结论。网页正文会安全缓存 `WEB_CACHE_TTL_HOURS` 指定的时长；缓存和重试不会绕过 DNS、跳转和内网地址限制。

深度研究默认每条查询取得最多 8 个结果，在全部查询间轮转选择最多 24 个不同网页候选，再读取可访问正文和筛选证据。可用 `.env` 中的 `WEB_RESULTS_PER_QUERY`、`MAX_WEB_CANDIDATES` 调整覆盖范围；提高它们会增加时延、模型上下文和搜索调用成本。工作空间只限制搜索次数和并发研究数；Token 使用会被记录用于成本观测，但不会作为“今日 Token 预算”拒绝用户请求。它是多源抽样与可核查综合，不是对整个互联网的穷尽抓取。

运行任务时，后端会向容器标准输出记录任务短 ID、节点开始/结束与耗时、模型调用、搜索结果数量、候选/可读网页数量、证据筛选数量和错误类别。查看命令为 `docker compose logs -f backend`。日志默认不记录问题文本、用户偏好、提示词、模型输入输出、URL 或网页正文；用 `TASK_LOG_LEVEL=WARNING` 可减少正常进度日志。

本地资料支持 TXT、Markdown、DOCX、文字型 PDF，以及 JPG/JPEG、PNG、GIF、WebP 图片。文本资料按标题、页码与 DOCX 表格结构切块；图片会先校验真实文件签名，再由 `VISION_MODEL_ID` 指定的 DeepSeek 视觉模型提取可检索文字、表格标题和图表事实，之后进入同一向量与 BM25 混合检索。扫描 PDF 仅在其嵌入图像可被安全提取时尝试视觉解析；PPT、Excel、复杂公式和通用版面理解仍不在当前范围。资料库页面可以查看 Chunk 定位、手动重建索引，并通过检索调试工作台查看向量、BM25 与融合分数。

默认不会自动把报告写入语义记忆。完成报告后可在界面点击“保存为语义记忆”，或设置 `AUTO_SAVE_SEMANTIC_MEMORY=true` 恢复自动保存。用户偏好和个人设定按 User ID 共享；报告语义记忆只会在保存它的 Thread 内被检索。工作台设置页可导出当前用户 ZIP 数据，或在确认后清理本地报告、记忆和资料。

左侧“最近研究”中每个会话都可删除。删除会停止该会话仍在运行的任务，并一并清除该会话的报告、事件、计数和检查点；本地资料、用户偏好和个人设定不会受影响。已单独保存的报告语义记忆仍会保留在数据导出中，但由于原 Thread 已删除，不会再被注入后续研究。

工作台的“演示工作空间”使用 User ID 隔离本地资料、记忆和会话；每个用户的新会话自动使用 `thread01`、`thread02` 等短 Thread ID，可直接用于切换。内部 UUID 仅用于数据库关联与检查点，不会显示在工作台。`APP_ACCESS_TOKEN` 是仅在 `.env` 中显式设置时启用的服务访问保护，不是演示身份，也不会显示在工作台界面。

运行无需外部 API 的评测基线：

```powershell
.\.venv\Scripts\python.exe .\scripts\evaluate.py
```

它在固定资料上输出路由正确率、来源数、引用检查、时延与 Token 基线到 `.cache/eval/`，不代表真实行业研究质量。

## 企业演示版

企业演示使用独立覆盖文件启动：

```powershell
docker compose -f compose.yaml -f compose.enterprise.yaml up -d --build --wait
```

该配置增加 PostgreSQL、Redis Worker、Keycloak、OpenTelemetry Collector、Prometheus 和 Grafana。后端只接受 OIDC JWT；浏览器提供的 `X-User-ID` 不作为生产身份。工作空间成员角色为 admin、researcher、viewer，审计日志不记录研究正文、来源地址或密钥。

当前 Compose 适合单机演示；运行、备份和恢复说明见 [docs/operations.md](docs/operations.md)。
使用下面的命令执行不涉及模型调用的集成冒烟检查。它会验证 Web、Keycloak OIDC 发现、PostgreSQL 迁移版本、Redis、Worker、后端 `/readyz` 和 OpenTelemetry Collector 的连通性：

```powershell
.\scripts\smoke-enterprise.ps1
```

首次启用企业版时，Keycloak 的 `keycloak-data` 命名卷会保存本机注册账号和管理台改动；不要用 `docker compose down -v` 停止演示环境。
Grafana 仅映射到 `http://localhost:3000`，使用 `.env` 的 `GRAFANA_ADMIN_PASSWORD` 登录；Prometheus 数据源会在启动时自动连接到容器内的 Prometheus。

## 企业演示验证

企业版使用 Keycloak、PostgreSQL、Redis Worker、Milvus、Prometheus、Grafana 与 ClamAV。启动后打开 `http://localhost:8080` 登录；监控面板在 `http://localhost:3000` 的 **Dashboards → DeepResearch**。Grafana 仅监听本机。

```powershell
.\scripts\smoke-enterprise.ps1
```

资料上传在企业版会先经 ClamAV 扫描；扫描服务不可用时上传会被拒绝。连续会话中的每条历史研究都有“查看来源 N”入口，可切换并查看该次研究保存的引用与本地资料来源。

`migrate` 是一次性 Alembic 迁移任务，显示 `Exited (0)` 表示成功完成，应保留；不要执行 `docker compose down -v`，否则会删除本机演示数据卷。

## 企业演示强化说明

企业版上传资料时先写入 `/data/quarantine`，再交由 ClamAV 扫描。只有 `ready` 状态的资料可检索、用于研究或导出：`scanning` 表示正在扫描，`indexing` 表示已通过扫描且正在建立索引，`quarantined` 表示扫描拒绝，`scan_failed` 表示扫描服务不可用。后两种状态保留最少的文件记录和错误说明，不能通过重建索引绕过扫描；管理员删除后会同时删除隔离文件，并写入不含正文的审计记录。

研究与资料索引均有持久化状态。企业版研究任务由 Redis Worker 消费，每次尝试使用固定的队列任务编号；重复恢复不会并行执行同一研究。点击停止会先将研究持久化为 `cancelled`，再向 Worker 发送中止信号，因此排队任务和已开始的任务都会停止，晚到的 Worker 也不能重新领取它。Worker 每 10 秒更新一次心跳；只有连续 45 秒未更新的运行中任务才会被标记为中断并从检查点重新入队。资料索引在 API 或 Worker 重启后会重新入队，已通过扫描的原始文件不会因进程内任务丢失而被视为可检索。任务、指标和追踪不使用用户、工作空间、主题、文件名、来源 URL 或错误正文作为 Prometheus 标签或 OpenTelemetry 属性。

Grafana 自动配置六个全局匿名面板：API/Worker 可用性、队列深度、任务成功率、失败类别、节点 P95 时延和 Token 消耗。刚重建容器且尚未运行研究时，计数器为零；运行一次研究后 Prometheus 的 15 秒抓取周期内会出现数据。

企业 Compose 冒烟在本机和 GitHub Actions 复用标准库脚本：

```powershell
.\scripts\smoke-enterprise.ps1 -Start
```

它不会调用模型或联网搜索，检查 Web、OIDC、迁移、Redis、`/readyz`、Worker 指标、Prometheus 两个抓取目标、Grafana 与 OTel Collector。

备份和恢复应在停止演示服务后执行。备份同时导出 PostgreSQL 逻辑 SQL 与研究附件、Milvus、Redis、Keycloak、Grafana 等命名卷，并生成 SHA-256 清单：

```powershell
.\scripts\backup.ps1
.\scripts\restore.ps1 -Input .cache\backup\deepresearch-时间戳 -ReplaceVolumes
```

恢复会替换当前项目的命名卷，完成后重新启动企业 Compose 并运行企业冒烟检查。不要将备份文件、`.env` 或密钥提交到 Git。

前端工具链依赖通过 Dependabot 分组升级，并以 `npm ci`、Vitest 和生产构建作为合并门槛。当前 Vue TSC 3.3.11 与 TypeScript 7.0.2 实际不兼容，因此项目固定 TypeScript 6.0.3，并暂时忽略 TypeScript 7 的自动升级；只有完成兼容性验证后才解除该限制。
