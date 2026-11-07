# DeepResearch 企业研究工作台

输入研究问题，从公开网页或授权资料中查找依据，生成带引用的中文报告；支持团队协作、资料权限、异人审核、冻结发布和报告修订。技术栈为 **Spring Boot + LangGraph + Vue 3**。

当前交付面向企业内部、单区域、多副本应用部署：平台默认 5 个执行研究任务、20 个等待任务，单空间最多 2 个执行任务、500 份资料和 5 万片段。容量目标为 50 个账号、10 个在线用户；目标负载、真实模型质量、备份恢复与云环境必须按[验收标准](docs/production-readiness.md)取得证据，不能仅凭容器健康判断可上线。

- 运行项目：[本地快速部署](#本地快速部署)。
- 接入云平台：[完整接入手册](docs/cloud-integration.md)、[生产化交付与验收](docs/production-readiness.md)。
- 本轮验证：[脱敏证据索引](docs/evidence/production-20261107/README.md)（四次OpenAI、双副本故障、30分钟容量及隔离逻辑恢复；不代表生产SLA）。
- 理解源码：[目录与调用路径](docs/project-structure.md)、[Java/Python 职责](docs/java-python-ownership.md)。

## 已实现的能力

| 方向 | 功能 |
| --- | --- |
| 研究 | 自动判断、快速问答、深度研究、范围确认、同会话追问、活动进度、停止/恢复 |
| 资料 | TXT/Markdown/DOCX/PDF/图片、扫描隔离、解析限制、向量/词法融合检索、集合授权与撤权 |
| 证据 | 引用原文、草稿下载、文档版本/差异、影响分析、正式报告冻结、异人审核与修订 |
| 模型 | OpenAI 对话/视觉/联网搜索与官方嵌入默认适配；BGE/SiliconFlow 重排保持现有实现 |
| 治理 | 平台管理页、已知账号停用/恢复、会话撤销、空间所有权交接、取消任务、用量/审计 |
| 运行 | Redis 共享限流、PostgreSQL 任务/预算/Outbox、跨副本准入、检查点、Worker 心跳/排空 |
| 接入 | OIDC、TLS PostgreSQL/Redis/Milvus、S3 临时凭据/工作负载身份/SSE、OTLP、Compose/Kubernetes |

“以后无需改代码”适用于[已支持的协议契约](docs/cloud-integration.md)：配置端点、凭据、证书、权限和部署参数即可接入。云资源创建、MFA/SMTP、托管备份、告警通知与异机恢复仍需平台配置和验证。未实现商业 SaaS、支付、任意厂商原生协议或跨地域多活。

## 本地快速部署

需要 Git、Docker、Docker Compose 和已安装安全补丁的 Python 3.11+。应用由 Docker 构建，不要求本机 Java/Node。固定镜像面向 Linux/amd64，Windows 使用 Docker Desktop Linux 容器。以下 PowerShell 命令在项目根目录运行。

```powershell
git clone --config core.autocrlf=false https://github.com/W205614/DeepResearch.git
cd DeepResearch
Copy-Item .env.example .env
```

已有安装先保留原 `.env`、数据卷和有效恢复点，不覆盖原配置、不删卷重置；查看[升级与维护](docs/deployment-security-operations.md)。

精确制品验证需要保留仓库输入字节。上述克隆命令在此仓库保存 LF 检出配置，适用于 Windows 和 Linux；带 `-text` 的原始证据仍保留自身字节。已有工作区若报告输入换行不匹配，另建按上述方式克隆的工作区验证，保留原工作区中的本地修改，不用强制检出覆盖。

使用编辑器填写 `.env`，凭据不提交仓库：

| 配置 | 说明 |
| --- | --- |
| LLM_API_KEY | OpenAI 项目密钥；默认 https://api.openai.com/v1、gpt-6.1-sol、low |
| EMBEDDING_API_KEY | 新安装使用同一 OpenAI 项目密钥；text-embedding-3-large、3072维 |
| RERANKER_API_KEY | 仍为原 SiliconFlow 凭据；未配可显式 RERANKER_ENABLED=false |
| POSTGRES_PASSWORD、KEYCLOAK_DB_PASSWORD、KEYCLOAK_ADMIN_PASSWORD | 项目数据库、身份数据库和身份管理台独立密码 |
| MINIO_ROOT_PASSWORD、GRAFANA_ADMIN_PASSWORD | 存储根账号与观测管理台独立密码 |
| MIGRATOR_DB_PASSWORD、JAVA_DB_PASSWORD、PYTHON_DB_PASSWORD、MONITOR_DB_PASSWORD | 迁移、Java、Python、监控分权账号，不共用管理员 |
| OBJECT_STORE_ACCESS_KEY、OBJECT_STORE_SECRET_KEY、MILVUS_STORAGE_PASSWORD | 业务资料和向量存储独立服务身份 |

可用 `python scripts/prepare_service_credentials.py --env .env` 仅补齐空的服务凭据，保留原配置备份，输出不含密码。已有安装分权升级步骤见[部署安全](docs/deployment-security-operations.md)。平台操作员由 `PLATFORM_OPERATOR_SUBJECTS` 配置 IdP 的账号 `sub`，逗号分隔，不是邮箱或 Keycloak 管理员用户名。

**历史索引不能直接换端点。** 本轮实际旧库有 118 个候选片段，保守输入估算 169746 Token；未执行重建。旧安装维持原 EMBEDDING_* 和其原凭据，按[受控影子迁移](docs/production-readiness.md#历史索引迁移与回退)核算、授权、迁移、验收后再切。不能把 OpenAI 密钥发送给旧第三方端点。新安装无历史向量可直接用模板的官方嵌入。

首次取得并校验批准镜像，再启动：

```powershell
python scripts/fetch_approved_image.py
python scripts/fetch_grafana_fixed_20261107.py
docker compose up -d --build --wait --wait-timeout 300
```

固定归档、OCI 描述符、层、配置及二进制会核对后才标记镜像。首次下载、构建和扫描初始化耗时取决于网络/机器；无网络可使用工具的 `--archive`。资源有限时使用 `docker compose -f compose.yaml -f deployment/compose/compose.core.yaml up -d --build --wait --wait-timeout 300` 关闭可选观测栈，此时不需要 Grafana 归档。资源上限不是容量保证。

| 入口 | 地址 | 登录 |
| --- | --- | --- |
| 工作台 | http://localhost:8080 | 本地 IdP 创建账号后登录 |
| Keycloak 管理台 | http://localhost:8180/admin | admin / KEYCLOAK_ADMIN_PASSWORD |
| Grafana | http://localhost:3000 | admin / GRAFANA_ADMIN_PASSWORD |

本地默认允许注册；企业云模板关闭注册入口，IdP 也须关闭自助注册或启用审批。Keycloak 管理台身份与平台操作员、空间管理员是不同权限。

## 完成一次研究

1. 登录工作台，首次创建个人空间。选择“公开问题（联网检索）”，输入明确问题，例如“比较线上与线下课程的差异、适用人群和局限，提供公开来源”。
2. 开始研究，查看进度，完成后核对一条引用及原文片段，再导出 Markdown。
3. 内部资料使用前，确认组织允许所配置模型/视觉/嵌入/重排服务处理资料，再设置 ALLOW_INTERNAL_MODEL_PROCESSING=true 并重启对应服务。上传文件，待扫描和索引 ready，再选择“内部资料（不联网）”。不联网表示不做公开网页搜索，模型处理仍可外发。
4. 在资料集合中设置受限授权。成员角色不自动取得所有受限资料权限；撤权后历史正文、记忆、来源与下载也重新校验。
5. 正式报告由作者提交，另一位有权限管理员审核，作者不能自审。批准后正文与证据冻结；修订形成新版本，保留原报告和历史作者。

单文件不超过 10MB，每次最多 4 张图片。公开模式不读取本地资料和私有历史；证据不足会显示部分结果或未回答问题。供应商调用、搜索和可选重排可能产生费用，预算耗尽会阻止下一次外发。

## 平台管理与多副本

配置为平台操作员的当前启用账号可见管理入口：账号/空间交接、停用/恢复、任务取消、运行状态、用量和治理审计。管理页不开放其他人的私人正文或密钥。停用前交接所拥有空间，不能停用自己、最后有效平台/空间管理员；恢复后须重新登录，旧令牌持续失效。原始创建者/历史作者保持不变。接口与约束见[账号治理](docs/production-readiness.md#账号治理和管理契约)。

Redis 故障时已建立账号的授权历史可读，新研究、上传、业务修改与新模型外发返回暂不可用；数据库故障不假接单。Worker 提供实际心跳、就绪、排空与有界退出，平台并发跨副本控制。多副本本地验证使用独立 project/env/卷的 [compose.replicas.yaml](deployment/compose/compose.replicas.yaml)；[compose.production-test.yaml](deployment/compose/compose.production-test.yaml) 只用于隔离模型替身测试，不能生产使用。

云端独立入口：

```powershell
Copy-Item deployment/cloud.env.example .env.cloud
# 按接入手册准备实际身份、分权数据库、Bucket、证书、Secret、私网扫描/队列/向量端点。
python scripts/check_production_config.py --env-file .env.cloud --output .cache/cloud-preflight.json
# 仅在目标部署环境完成配置及放行后执行：
docker compose --project-directory . --env-file .env.cloud -f deployment/compose/compose.cloud.yaml up -d --build --wait --wait-timeout 300
```

应用层默认双副本，Nginx 分发，不重新部署单机数据组件。离线预检不访问供应商、不产生模型费用，configuration_valid 不等于 production_approved。Kubernetes 清单使用外部数据端点、ConfigMap/Secret、独立迁移 Job、探针、滚动更新和中断预算；[分阶段部署与集群 DNS](docs/cloud-integration.md#kubernetes-应用层)需按手册处理，不能一次应用整套含迁移的资源。

## 可复现的隔离验收

[`scripts/production_acceptance.py`](scripts/production_acceptance.py) 提供固定 dr-production-test project 的可复用入口；不会读取私密 `.env`，只生成带标记的公开假凭据配置，Java/Python 均允许受控内部资料流程。先按[既有安全镜像流程](docs/deployment-security-operations.md)准备已批准 MinIO、Milvus、etcd 等 artifact，不因重新构建就视为重新安全批准。

```powershell
# 离线生成 .env.production-test，不启动 Docker；已有非本工具管理文件会拒绝覆盖。
python scripts/production_acceptance.py prepare
# 默认用仓库 canonical Dockerfile 构建当前源码与独立 candidate tags。
python scripts/production_acceptance.py smoke --isolated
# 显式复用该隔离栈，运行完整30分钟/500资料/5万片段及故障演练。
python scripts/production_acceptance.py all --isolated --reuse-stack
```

工具创建50个真实测试 OIDC 身份，固定四个 Compose 文件，保留每次独立的 `.cache/production-acceptance/<run_id>/` 报告、构建/执行日志、令牌和数据，默认不停止或删除隔离服务；这些令牌同样需要私密保存。已有其他维护者创建的 `.env.production-test` 请先自行保留，不以此工具覆盖。也可分别执行 load/faults；完整步骤及严格镜像复用边界见[验收入口](docs/production-readiness.md#可复现验收入口)。

需要明确复用预建镜像时加 `--reuse-images --image-baseline "基线说明"`，记录实际image/container IDs，不能宣称验证了最新源码的canonical构建。本轮从已维护依赖镜像复制源码的临时候选属于此类明确复用；新的标准入口默认重新canonical构建。该入口本轮仅离线/配置与帮助测试，尚未据此完成完整生产放行。

## 验证与运行边界

本轮文字、合成图片、联网搜索、单条 3072 维嵌入四次真实连接均成功；持久 ledger 禁止重复调用。完整新模型质量、历史重建、日常部署、真实云与异机恢复没有因此通过。相关命令、预算、容量和故障放行见[生产验收](docs/production-readiness.md)。

[研究工作台](docs/images/workspace.png) · [报告与证据](docs/images/research-report.png) · [正式审核](docs/images/report-review.png)

截图为固定接口响应与人工长报告排版示例，未运行真实模型/审核，不作为生产验收。

常见问题：启动失败先看 `docker compose ps -a` 和对应服务日志，migrate Exited(0) 是成功退出；研究失败核对服务连接、端点/模型、账号权限和预算；扫描不可用资料继续隔离；登录回调修改须同步 IdP redirect_uri/Web Origins；已有 Grafana 密码不因环境变量自动更新。不要通过删数据卷排错。

停止并保留数据：`docker compose down`，不要加 `-v`。本机备份当前保持用户停用状态；上线前须配置并实际演练有效恢复点，不能把目录存在当作备份成功。

## 更多资料

| 主题 | 文档 |
| --- | --- |
| 接入与上线 | [接入手册](docs/cloud-integration.md) · [生产化验收](docs/production-readiness.md) · [云配置](deployment/cloud.env.example) |
| 使用 | [登录](docs/local-keycloak-login.md) · [多模态](docs/multimodal.md) · [资料/报告生命周期](docs/evidence-lifecycle.md) |
| 开发 | [目录](docs/project-structure.md) · [职责边界](docs/java-python-ownership.md) · [评测数据](eval/lifecycle/README.md) |
| 运维 | [安全、镜像、备份](docs/deployment-security-operations.md) · [监控与告警](docs/production-observability.md) · [本地维护](docs/local-pilot-operations.md) |
| 历史证据 | [交付索引](docs/delivery-history.md) · [检索评测](eval/benchmark_results.md) · [安全维护](docs/security-maintenance-20261105.md) |
