# 生产化交付与验收

适用日期：2026-11-07。面向企业内部团队，单区域、多副本应用层；沿用 Spring Boot、LangGraph、Vue、PostgreSQL、Redis、Milvus 和 S3。容量目标：50 个账号、10 个在线用户、全平台 5 个执行研究任务、20 个等待任务，单空间最多 2 个执行任务、500 份资料及 5 万片段。

当前代码基线 `b8153f5` 的[CI](https://github.com/W205614/DeepResearch/actions/runs/42370035398)、[Security 22个作业](https://github.com/W205614/DeepResearch/actions/runs/42370035403)和[告警规则](https://github.com/W205614/DeepResearch/actions/runs/42370035381)均通过。个人本地功能交付可进入按需维护阶段；目标云平台接入、完整新模型质量与异机恢复按本文分别验收。

## 交付入口与证据

| 领域 | 已实现入口 | 验证边界 |
| --- | --- | --- |
| 模型 | OpenAI Chat Completions、Responses web_search、官方 3072 维嵌入；保留 BGE/SiliconFlow 重排 | 四类真实连接请求均 HTTP 200；完整模型质量未评测 |
| 索引迁移 | 只读检查、累计预算、持久进度、影子集合、显式激活/中止 | 真实历史只读评估完成；没有全量重新嵌入或日常切换 |
| 多副本 | Redis 共享限流、PostgreSQL 准入/执行状态、Outbox、执行轮次和迟到结果保护 | 本地双副本负载、故障与恢复已实测；目标云环境另行复测 |
| 账号治理 | 停用/恢复、旧会话撤销、当前所有者、交接、取消、治理审计和管理页 | 本地双Java/双Python真实OIDC验证通过；目标IdP策略与真实入口待验收 |
| 云接入 | [接入手册](cloud-integration.md)、[配置模板](../deployment/cloud.env.example)、[应用 Compose](../deployment/compose/compose.cloud.yaml)及 Kubernetes 应用资源 | 真实网络、权限、CA、工作负载身份、配额、备份待验收 |
| 运维 | Worker 心跳/就绪/排空、指标告警、迁移、负载和故障工具 | 未取得恢复点、质量和异机恢复证据前不能放行日常上线 |

脱敏结果见[本次证据索引](evidence/production-20261107/README.md)。原始本机报告位于 `.cache/openai-smoke/`、`.cache/production-integrations/` 和 `.cache/production-acceptance/`，其中凭据、令牌、业务备份与原文不提交仓库。历史评测保留原模型、提交和账本；当前结果记录在相应执行报告中。

## OpenAI 配置与四次真实验证

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_ID=gpt-6.1-sol
VISION_MODEL_ID=gpt-6.1-sol
LLM_REASONING_EFFORT=low
LLM_EXTRA_BODY={}
WEB_SEARCH_PROVIDER=openai
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-large
EMBEDDING_DIMENSION=3072
SEND_EMBEDDING_DIMENSIONS=true
```

新安装将同一 OpenAI 项目密钥分别注入 `LLM_API_KEY` 和 `EMBEDDING_API_KEY`；运维脚本读取 `OPENAI_API_KEY`。仅使用本地私密配置、Secret 或秘密管理平台，密钥保存在本地私密配置、Secret或秘密管理平台。**有历史索引的旧部署保留原嵌入配置及其原密钥，完成迁移后再切换。** 旧第三方嵌入端点使用其原独立凭据。BGE/SiliconFlow 重排仍用原独立端点、模型与凭据。

文字/视觉使用 `max_completion_tokens` 和 `reasoning_effort=low`，保留既有输出与上下文上限；不发送旧 temperature、top_p、logprobs 或 DeepSeek thinking 参数。输出用量包含推理 Token，预算预留覆盖输出上限。搜索解析官方原生引用和 `web_search_call.action.sources`，不采信正文任意 URL。OpenAI 模式失败明确报错，不自动回退 DeepSeek/Bocha。

```powershell
python scripts/openai_smoke.py --env-file .env
```

默认 ledger 为 `.cache/openai-smoke/requests.sqlite3`，报告为 `.cache/openai-smoke/report.json`。先持久化次数，再依次发出文字、合成红色图片、公开联网问题和单条 3072 维嵌入；每类最多一次 HTTP，禁重试/重定向，失败即停，未知结果/中断同样消耗次数。再次执行沿用 ledger，不补打失败类别；不要更换或删除 ledger 绕过额度。

本轮这四类均 HTTP 200：文字总用量 30、图片 48、联网搜索 8734、嵌入 4 Token，单条嵌入确认 3072 维。这些数字对应本次供应商响应。完整质量评测与历史向量重建使用独立预算。

协议依据：[Sol 模型](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[请求参数](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[联网搜索](https://developers.openai.com/api/docs/guides/tools-web-search)、[嵌入](https://developers.openai.com/api/docs/guides/embeddings)。模型账号权限和配额以实际项目为准。

## 历史索引迁移与回退

指纹包含模型、端点、维度和演示模式，名称/维度相同也不能混用端点不同的向量。工具保留旧集合、资料、版本、作者、报告和引用，不覆盖旧指纹绕过检查。

2026-11-07 已停止的日常 PostgreSQL 卷经只读挂载复制到独立卷，在无网络、默认只读的隔离 PostgreSQL 查询后停止。该次检查的源schema为 `0017_product_governance`；4 份 ready 资料、118 个候选片段、0 条语义记忆，另有 8 份删除资料、1 份失败资料和 2 条非语义个人记忆。UTF-8 字节与框架开销保守估算输入 169746 Token，**并非供应商计费量或报价**；单条模式预计 118 次请求，实际预算还含请求 payload 预留和平台额度。旧 Milvus 集合本轮未核验。脱敏报告：`.cache/production-integrations/history-embedding-inspection.json`。

在根目录使用项目 Python 依赖。私密注入 `MIGRATOR_DATABASE_URL`，运行重建还需 `OPENAI_API_KEY`；env 文件中的 EMBEDDING_* 准确描述旧索引，目标固定为官方 3072 维。迁移写入使用独立 dr_migrator，运行时 dr_python 只读迁移表。

```powershell
# 只读检查，不发模型请求、不初始化数据库，兼容旧 schema。
python scripts/embedding_migration.py --env-file .env inspect
# 可加 --database-env PYTHON_DATABASE_URL 选择运行时读取连接。

# 先取得恢复点、完成增量 schema 迁移，并在维护窗口停止业务修改和在途任务。
python scripts/embedding_migration.py --env-file .env prepare --maintenance-acknowledged
python scripts/embedding_migration.py --env-file .env progress <migration_id>

# 另行授权的付费重建；根据检查结果批准累计额度后替换下列参数。
python scripts/embedding_migration.py --env-file .env run <migration_id> --subject <operator_subject> --allow-paid --max-requests <cumulative_requests> --max-reserved-tokens <cumulative_reserved_tokens>
python scripts/embedding_migration.py --env-file .env activate <migration_id> --maintenance-acknowledged
```

prepare 要求无 queued/running/interrupted 研究和 scanning/indexing/rebuilding 资料，设置持久维护屏障。记录源文本/权限摘要、逐项进度/失败、累计请求及预留。账号须在 PLATFORM_OPERATOR_SUBJECTS 内、启用且对相应空间及受限资料有当前写权限，并已批准 ALLOW_INTERNAL_MODEL_PROCESSING。run 续跑跳过已完成项，多次命令不会重置额度。失败项需显式 `--retry-failed`；未知/中断项核对供应商和账本后需显式 `--retry-unknown`，仍计预算。没有请求自动重试。

激活检查实际影子集合的覆盖、维度、空间归属与来源摘要；持有源表锁，在同一数据库事务重查任务、权限、源数据与屏障，再原子切换。期间普通业务写入和新模型外发被阻止。激活后将所有 Python 服务配置改为目标官方 EMBEDDING_* 和对应密钥，重启并核对后恢复流量；旧配置实例因指纹不匹配拒绝混用。

切换前可执行 `abort <migration_id>` 解除本次屏障并保留旧指纹、全部影子集合。已激活不能用 abort 回滚：保持维护窗口，恢复预先保存的一致数据库/对象/索引恢复点及匹配的原配置，核验后恢复业务；不要手工只改指纹。新索引接收业务更新后旧集合已落后，单独切回会漏资料。任何旧/影子集合清理均须另行明确授权。

新安装无历史向量可直接初始化官方索引；旧安装未批准重建时只切文字/视觉/搜索，日常嵌入维持原状。历史重建需按118个片段另定累计调用与Token预算。

## 多副本、降级与 Worker

PostgreSQL 是任务、预算、审核、账号、所有权事实源；Redis 负责共享限流与队列。全局/空间准入、领取、重复 Outbox、取消/恢复、迟到结果由共享状态控制，不要求会话粘滞。两个研究 Worker 各最多 3 个任务，共享上限保证平台执行数不超过 5；每实例还有限流/线程/连接保护。

Redis 故障保留**已建立账号且当前权限有效**的历史读取，使用本机资源保护；新研究、上传、业务修改和新模型外发返回暂不可用。新账号首次空间引导需要写入，因此可能返回 503。数据库故障不以空数据或缓存冒充正常查询/接单。历史读取仍检查当前账号、成员、受限集合和报告权限。就绪与入口设置下的真实降级需目标环境演练。

Worker 入口 `python -m backend.worker research|documents`；`python -m backend.core.worker_health --ready|--live|--drain` 查看/设置状态。每 5 秒心跳，ready 要求数据库/队列可用且非排空，live 要求 15 秒内心跳与真实进程。SIGTERM 或 drain 停止领取，按 WORKER_DRAIN_SECONDS（默认 30 秒）等待；剩余任务转 interrupted 并保留恢复状态。探针、终止宽限与任务预算须在编排和平台一致。

## 账号治理和管理契约

PLATFORM_OPERATOR_SUBJECTS 是 IdP sub 的逗号分隔受控白名单，空间管理员不能自行取得平台权限。管理页只展示治理元数据、运行、用量和审计，不授予私人正文、密钥或所有资料的阅读权。用量读取最近有记录的platform计量周期，不重复累加subject/workspace/account维度，不将Token当作账单金额；无记录与未探测依赖明确显示。账号列表仅为本应用已知账号。

| 接口 | 参数/行为 |
| --- | --- |
| GET /api/auth/session | 当前 subject、status、platform_operator |
| GET /api/admin/overview | 运行/容量/依赖/降级 |
| GET /api/admin/users | cursor、limit（1..100）；返回 items/next_cursor |
| PUT /api/admin/users/{subject}/status | body：status=active 或 disabled、reason（非空，最多1000字符） |
| POST /api/admin/workspaces/{id}/transfer | body：target_subject（最多128字符）、reason |
| POST /api/admin/runs/{id}/cancel | body：reason；复用任务状态机 |
| GET /api/admin/audit | cursor、limit、actor、action、from、until（ISO Instant）；items/next_cursor |

禁止停用自己、最后有效已知平台管理员、仍拥有空间或作为空间最后有效管理员的账号。交接目标须为已知启用账号，新所有者成为 admin，原所有者降为 researcher；原始 created_by 和历史作者不变。停用/恢复都会撤销旧 iat，恢复后须重新登录，旧令牌继续失效；生产 JWT 必须有 iat。Java/Python、事件流、分块下载和模型外发重新检查账号，在途任务不继续发布结果，但无法撤销已发出的供应商请求或费用。IdP 的 SSO、离职、MFA、SMTP/账号恢复由组织同时配置实测。

## 部署与发布验收

本地多副本使用 compose.yaml + compose.core.yaml + compose.replicas.yaml，独立 project/env/端口/数据卷。compose.production-test.yaml 仅隔离验证，模型是替身，不能生产使用。云端 compose.cloud.yaml 是独立应用层编排，web/backend/agent/两类 Worker 各默认双副本，Nginx edge 与 Agent gateway 负载分发，外接 PostgreSQL/Redis/Milvus/S3/IdP/ClamAV。Kubernetes 按独立迁移 Job 后再滚动应用的顺序操作；详细配置见[接入手册](cloud-integration.md)。

`scripts/production_load.py` 需 `--isolated`、INTEGRATION=1、真实 PG/Redis/Milvus、3072 维、私密 OIDC `--tokens-json`。目标参数 `--seconds 1800 --accounts 50 --clients 10 --research-concurrency 5 --documents 500 --chunks-per-document 100`；实际上传扫描解析后核对 50000 SQL/Milvus 向量，再开始计负载。核对重复请求、完成事件、控制 API P95、错误率、执行/队列峰值和任务收敛；数据/报告保留。短测 functional_passed 不等于 production_capacity_passed。`scripts/production_faults.py` 面向固定 dr-production-test 隔离环境，验证 Redis 只读、单 API 副本退出与 Worker 中断恢复，禁止指向日常 project。

| 项目 | 放行条件 |
| --- | --- |
| 一致性 | 重复请求/投递无重复结果，任务/预算/审核无法并发绕过，单副本退出仍服务 |
| 账号 | 两副本拒绝停用账号，旧会话失效，交接保留历史作者 |
| 故障 | Redis 故障授权历史可读且拒写，Worker 中断恢复，数据库不假接单 |
| 容量 | 50账号/10在线/5研究并发/500资料/5万片段，混合负载至少30分钟 |
| 控制 API | P95 < 2秒，非预期错误 < 1%，所有已接单任务可核对收敛 |
| AI质量 | 用例 ≥85%、引用支持 ≥95%、答案覆盖 ≥85%，关键用例不得失败 |
| 恢复 | 隔离恢复核对账号权限、资料、引用、正式报告哈希/下载，记录实测RPO/RTO |
| 发布 | 最终源码相关本地检查通过，提交后核验对应GitHub CI/安全结果 |

本地负载使用受控模型替身；真实模型速度、费用与质量在供应商环境单独评测。部署前准备有效恢复点，完成必要索引迁移、质量、告警送达与异机恢复验证。日常容器升级和自动备份启用分别安排。

## 可复现验收入口

`scripts/production_acceptance.py prepare|smoke|load|faults|all` 固定 project=dr-production-test、`.env.production-test` 及 base/core/replicas/production-test 四文件。prepare 只创建带标记、内容固定的假配置，绝不读取私密.env或覆盖未知文件；继承进程中的模型/云凭据和COMPOSE覆盖变量不会进入子进程。启动动作须显式 --isolated，存在同一checkout隔离容器时还须 --reuse-stack；别的checkout同名project拒绝复用。

没有匹配容器但计划使用的命名卷已经存在时，入口会在构建和启动前拒绝执行；新假配置不能证明旧卷可以复用。保留这些卷，核对其原配置和归属，改用独立的新卷名进行验收；不要为绕过检查删除旧卷或重置存储身份。既有 `--reuse-stack` 不授权复用失去容器关联的卷。

默认 compose build 使用仓库 canonical Python/Java/Web/PostgreSQL/Keycloak/storage-init Dockerfile，测试专用 dr-production-*:candidate tags 不替换日常image tag。源树在构建前后核对。已有批准MinIO/Milvus/etcd等本地artifact需按原安全流程加载；只有锁定digest可拉取，入口不擅自重批安全结论。--reuse-images必须同时给非秘密--image-baseline说明，报告记录实际镜像和容器ID，并明确未验证最新源码。

smoke 是短时、小资料功能检查；load 固定1800秒/50账号/10在线/5研究并发/500资料×100片段，预置资料有界等待后才计时，默认每批2份、最多4份，不放宽产品队列/并发/资源阈值。faults复用现有Redis只读/单副本退出/Worker恢复工具；all按smoke→load→faults执行。每次实际准备50个隔离OIDC测试身份，令牌存私密receipts文件，不打印令牌。结果、manifest、构建/执行日志和数据均保留，失败不被计为通过，不自动清理服务或卷。

本轮该入口只完成7项离线测试、--help与四文件只读Compose渲染，未操作当时正在联调的隔离环境。此前直接运行的30秒短测为50账号、10客户端、5研究并发、5份资料/20向量：496请求0非预期错误，19/19任务收敛、控制P95 176.72ms；production_capacity_passed=false。正式500资料/50000 SQL片段与Milvus向量已完成1806.62秒混合负载：17209请求，601接单任务全部收敛（包含证据不足），62次重复提交身份复用，0重复/遗漏完成事件；执行峰值5，等待峰值5。控制API P95=210.95ms，8个503导致非预期错误率0.0465%，符合既定<1%门槛。该轮检索P95=24652.72ms，不能把控制API指标写成所有接口时延。

诊断发现完整词法评分发生在既有缓存查验之前。最小修复将有效缓存查验前置，保留当前权限/版本、逐来源及邻块授权校验；未命中仍走原查询。没有改schema、索引、work_mem、资源或验收阈值。29项直接回归通过后，同一500资料/5万片段进行183.02秒短测：3283请求0非预期错误，210任务全部收敛，控制P95 53.42ms、检索P95 300.92ms、执行峰值5，0重复/遗漏完成事件。短测 `production_capacity_passed=false`，不替换原30分钟报告，也不证明冷缓存或真实模型时延。完整记录及版本边界见[本地验证汇总](evidence/production-20261107/local-validation.md)。

## 保留、告警与恢复

业务证据默认保留，缓存有界清理和归档不等于业务删除；清理对象、向量、检查点及关联引用须显式授权。Bucket 历史版本生命周期、数据库 PITR、身份/索引备份由平台配置，删除当前对象不代表抹除所有版本。

复用 observability/alerts.yml，配置私网采集、送达渠道、负责人及接单失败、积压、心跳、预算、扫描和备份新鲜度告警。本机备份保持停用；目标平台接入真实备份指标并验证通知送达。

恢复顺序：隔离写入并保留现场 → 恢复匹配身份/签名信任 → PostgreSQL/检查点/权限 → 对应 S3 对象/版本 → 匹配维度/活动指纹的索引 → Redis 与 Outbox/状态机恢复 → 只读核验 → Worker/入口。使用独立数据库、Bucket、Milvus 库/前缀、project 和隔离凭据，禁模型外发/通知，核对受限资料撤权、正式报告哈希、引用和下载后再开放修改。Redis 不能替代数据库事实源。

scripts/recovery.py 面向单机卷冷备，不自动执行托管云恢复；PITR、S3版本、IdP及向量快照通过所选平台控制面操作。升级保留增量schema，不做自动破坏性降级，不将旧镜像混用新版权限/配置作为回滚。每次恢复演练记录实际RPO／RTO和业务核验结果。

新增 `scripts/external_recovery.py` 可对支持协议做逻辑 snapshot/restore/verify；本轮真实隔离演练核对54张表、原文对象及向量、3个有效登录主体、停用拒绝、只读拒写、3次正式报告SHA/引用/下载。该次使用同机隔离Keycloak；IdP原生备份与异机恢复留待目标环境验证。参数、私密配置、PG客户端前置条件及未验边界见[外部恢复工具](cloud-integration.md#外部逻辑恢复工具)。真实身份恢复仍由目标IdP平台完成，工具不替代任意云厂商控制面。

私网采集、37条规则、托管备份指标与告警送达配置详见[生产监控与告警](production-observability.md)，本机与托管备份使用各自的状态指标和处置流程。
