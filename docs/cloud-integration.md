# 云平台接入手册

本手册描述已实现客户端与部署契约，适用于 2026-11-07 的企业内部单区域多副本版本。支持协议内只需配置端点、凭据、证书和部署参数，不需另写应用代码；目标平台资源、权限、配额、MFA/SMTP、备份、通知及验收仍由部署方配置。占位域名、Secret 模板和离线配置通过都不是已接通真实云的证据。

## 接入顺序和秘密注入

1. 准备真实域名/TLS、IdP SPA 客户端、专用数据库和分权角色、单主 Redis、Milvus 数据库、私有 S3 Bucket、私网 ClamAV、OTLP/Prometheus。
2. 新安装复制 deployment/cloud.env.example 到已忽略的 `.env.cloud`；已有历史安装先备份并保留旧嵌入配置，按[迁移步骤](production-readiness.md#历史索引迁移与回退)处理。
3. 将可信 CA 放到 `CLOUD_CERTIFICATES_DIR=./.env.cloud-certificates`，该 `.env.*` 目录同时被 Git 和 Docker 构建上下文排除，也可用仓库外绝对路径。Compose 只读挂到 `/run/certs`；不要放应用源码目录或构建上下文。
4. 通过秘密平台、容器环境或 Kubernetes Secret 注入凭据。只给需要的服务，不把整个私密 env_file 注入所有服务；web 仅收公开身份来源与集群 DNS 配置。迁移账号不能传给日常服务。
5. 运行离线预检，取得恢复点，单独完成迁移，再启动/滚动应用，最后做权限、故障、恢复和容量验收。

静态环境变量不是热更新接口。轮换先创建新凭据并授权，更新对应 Secret/私密文件，排空与滚动重启使用方，验证读写/队列/下载后再撤销旧凭据；未知请求保留账本，不强行重发。Boto3 工作负载身份由 SDK 按标准身份链更新，平台负责文件/端点可达与角色轮换。不要输出原始 compose config、DSN、完整环境或供应商响应来留证据。

## PostgreSQL 分权、TLS 和迁移

准备全新专用数据库，由有建角色和 schema 管理权限的云数据库管理员执行：

```powershell
# psql15+；PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD 用私密环境提供。
# PGSSLMODE=verify-full；私有CA设 PGSSLROOTCERT。
# 四个独立至少16位密码：MIGRATOR_DB_PASSWORD/JAVA_DB_PASSWORD/PYTHON_DB_PASSWORD/MONITOR_DB_PASSWORD。
psql -X -f deployment/postgres/cloud-bootstrap.sql
```

脚本在一个事务建立 dr_migrator/dr_java/dr_python/dr_monitor 和 schema 授权，不要求数据库 SUPERUSER。已有角色或应用表会失败回滚，它不是旧数据库导入/升级脚本。受限云账号由平台管理员按脚本等价授权，不给应用管理员权限。

| 使用方 | 参数 |
| --- | --- |
| Python运行 | PYTHON_DATABASE_URL=postgresql://dr_python:URL编码密码@主机:5432/库?sslmode=verify-full&sslrootcert=/run/certs/postgres-ca.pem |
| Java运行 | JAVA_DATABASE_URL=jdbc:postgresql://主机:5432/库?sslmode=verify-full&sslrootcert=/run/certs/postgres-ca.pem；JAVA_DB_PASSWORD 独立 |
| 迁移 | MIGRATOR_DATABASE_URL=postgresql+psycopg://dr_migrator:URL编码密码@主机:5432/库?sslmode=verify-full&sslrootcert=/run/certs/postgres-ca.pem |

三个连接必须同一数据库且凭据不同。JDBC 不应只写 verify-full 却漏配信任根；证书 DNS 必须匹配主机名。Python/Java 日常角色分权由 scripts/migrate_services.py 授予，治理表状态由 Java 管理，Python 不得写 status/revoked_before，迁移表只读，审计只追加。监控只读必要状态列。

应用之前执行 scripts/migrate_services.py：Alembic → 有界词法回填 → 检查点结构/权限。0018/0019 增量增加治理/当前所有者及嵌入迁移账本，不重置数据。已有库迁云另做一致性导出、对象/身份迁移和维护窗口；空云库不是原环境直接替代。检查点和正式报告必须与业务数据同一恢复点核对。迁移失败停止发布，不让应用先接单。

验收：按角色尝试合法操作与越权写治理/迁移/审计，错误 CA/主机名应失败；真实事务与重连后查询一致，数据库不可用时不假成功。托管 PITR/备份和恢复时点在平台配置并实际隔离恢复。

## OIDC 与账号治理

创建公开 SPA 客户端，开启 Authorization Code + PKCE S256，精确配置 HTTPS 根路径登录/退出回调和 Web Origins。访问令牌须是 RS256 签名 JWT，含正确 iss/aud/sub/exp/iat；不支持不透明访问令牌。主体 sub 必须稳定且不超过128字符；成员管理输入沿用字母、数字及 . _ : @ - 的标识约束，接入前用组织实际账号验证映射，不能把可变邮箱展示名当作稳定主体。

配置 OIDC_ISSUER、OIDC_JWKS_URL、OIDC_AUDIENCE、OIDC_CLIENT_ID；OIDC_SCOPES 默认 openid profile email，按 IdP 增加 API scope/offline_access。Java 与 Python issuer/audience 一致。前端 `/api/auth/config` 获公开配置，Discovery 找授权/令牌/可选退出端点，发现 issuer 必须匹配。OIDC_CONNECT_ORIGINS 为空格分隔的准确 HTTPS Origin，覆盖 discovery/token 端点，不接受通配符或完整路径。部署根路径，不在子路径挂载。

云模板 OIDC_REGISTRATION_ENABLED=false；IdP 自身也必须关闭注册或组织审批。没有 RP logout 端点时工作台只能清本地会话，SSO 会话按 IdP 策略处理。MFA、SMTP、账号找回/锁定、组同步在身份平台配置；不自动把 IdP 分组提升为管理员。

平台操作员由 PLATFORM_OPERATOR_SUBJECTS 的逗号 sub 白名单控制；空间权限独立。停用/恢复撤销旧会话，停用前交接所有权，保护最后管理员。管理接口、请求体和审计见[治理契约](production-readiness.md#账号治理和管理契约)。账号列表只覆盖本应用见过的主体。

公共 CA 使用运行镜像信任库。私有 IdP CA 通过只读证书挂载并配置 OIDC_CA_CERTS=/run/certs/oidc-ca.pem；配置非空而文件不存在会启动失败，不能关闭 TLS 验证。Java 不读取 OIDC_CA_CERTS：使用只含公开信任根的 PKCS12 文件（如 /run/certs/oidc-truststore.p12），通过 JAVA_TOOL_OPTIONS 配置 -Djavax.net.ssl.trustStore=/run/certs/oidc-truststore.p12 和独立的 -Djavax.net.ssl.trustStorePassword。JVM会打印该选项，不得复用API或数据库密码；这只是信任库的完整性密码，文件不包含私钥。浏览器仍须在自身系统信任身份平台证书。验收浏览器 discovery/PKCE、刷新/退出、无iat令牌拒绝、双副本停用与旧会话持续失效。

## Redis 单主与共享控制

REDIS_URL=rediss://ACL用户:URL编码密码@主端点:端口/库号；Python ARQ 和 Java Lettuce 共用同一数据库，所有 Java 副本 DEPLOYMENT_ID 一致。TLS 验证证书/主机名；私有 CA 配 REDIS_SSL_CA_CERTS=/run/certs/redis-ca.pem。不要在 DSN 加被忽略的 TLS 查询参数。支持单主或托管主端点，不宣称 Redis Cluster/Sentinel 协议适配。

ACL 需要 ARQ 队列/任务/健康与共享限流所用操作，允许 Lua EVAL/EVALSHA 及其底层命令；为本部署使用专用 Redis 实例/托管数据库端点，限制 `arq:*` 和 `dr:<DEPLOYMENT_ID>:api-limit:*` 等实际键前缀，并在隔离环境验证最小命令集。不授予 FLUSHALL/CONFIG。DEPLOYMENT_ID只隔离限流键，不改变ARQ队列名；不同部署不能共享同一逻辑数据库的ARQ队列。Redis Open Source的逻辑DB号不是独立应用安全边界，见[官方SELECT说明](https://redis.io/docs/latest/commands/select/)。云平台禁脚本时共享限流无法工作，不应静默改为每实例限流。

Redis 故障保留已建立账号的授权历史读，所有业务修改/新模型外发暂不可用；首次空间引导也因写入而失败。数据库保留事实源和 Outbox，恢复队列不能以“新建空库并忽略未完成任务”代替。验收真实断连、双副本共享限流/队列限额、重启后唯一结果与任务收敛。

## Milvus 索引

MILVUS_URI、MILVUS_TOKEN、MILVUS_DB_NAME、VECTOR_COLLECTION_PREFIX 在 API 和 Worker 一致。使用真实 HTTPS 端点，私有 CA 配 MILVUS_SERVER_PEM_PATH=/run/certs/milvus-ca.pem。生产、演练与迁移集合前缀隔离；服务身份仅授权目标数据库/集合范围内必要的描述、建集合/索引、读取、检索、upsert/delete，不使用集群管理员。

新安装默认 text-embedding-3-large/3072。模型、端点、维度变化先迁移，不手改指纹。迁移工具建立独立 dr_migration_<id> 集合，真实核对维度、覆盖、空间与源摘要后激活；旧索引保留，清理需另授权。验收认证、错误 CA/维度拒绝、不同空间隔离、真实检索，以及旧配置拒绝混用。云端数据库/集合需先按厂商权限准备。

## S3 对象存储与工作负载身份

预建私有 Bucket，OBJECT_STORE_BUCKET/REGION 必填，云端 OBJECT_STORE_AUTO_CREATE_BUCKET=false。AWS S3 可留空 OBJECT_STORE_ENDPOINT 用区域端点，其他兼容实现填 HTTPS。OBJECT_STORE_ADDRESSING_STYLE=auto|path|virtual 按提供方配置。自定义 CA 配 AWS_CA_BUNDLE=/run/certs/s3-ca.pem，默认系统 CA；不禁校验。

认证择一：

- 静态/STS：同时配置 OBJECT_STORE_ACCESS_KEY 与 OBJECT_STORE_SECRET_KEY；临时凭据再配 OBJECT_STORE_SESSION_TOKEN。轮换后重启使用方，平台负责有效期。
- AWS 工作负载身份：三者均空，Boto3 标准身份链。IRSA 类由平台注入 AWS_ROLE_ARN、AWS_WEB_IDENTITY_TOKEN_FILE 并挂载投射身份文件；ECS 类使用标准凭据端点/授权文件变量。Compose 不自动签发或挂载云身份，Kubernetes 也需按平台为 service account 配角色和投射文件，不能只填写角色名。

最小范围：Bucket ListBucket/HeadBucket，以及 uploads/*、quarantine/* 的 GetObject/PutObject/DeleteObject；CopyObject 需源读和目标写。应用无需 CreateBucket，默认继承 Bucket 加密；显式配置 OBJECT_STORE_SSE=AES256 或 aws:kms，后者可设 OBJECT_STORE_KMS_KEY_ID 并授予对应 Encrypt/Decrypt/GenerateDataKey 等平台要求。上传与隔离转正复制携带加密参数。

SDK 失败返回 storage_unavailable，只有真实404/NoSuchKey等才视为缺失。版本控制/保留/生命周期由 Bucket 平台配置，删除当前对象不等于删除所有旧版本。验收真实上传→隔离扫描→转正复制→读取/下载→授权删除，以及无权限/到期凭据/网络故障明确失败；保留受控样本，禁止对日常证据做破坏性试验。

## 模型、扫描、观测与入口

模型默认与四次探测见[生产文档](production-readiness.md#openai-配置与四次真实验证)。LLM_PROVIDER=openai 使用官方参数；compatible 仅供显式兼容协议，不能只凭“OpenAI兼容”推断视觉、JSON、usage、dimensions或Responses搜索可用。OpenAI 模式不自动换供应商。BGE/SiliconFlow 的 RERANKER_URL/MODEL/API_KEY/文档格式保持原契约，OpenAI 密钥不传给它。

ClamAV 配 CLAMAV_HOST/PORT，INSTREAM 无认证，必须私网，不能公网暴露3310。扫描不可用资料继续隔离，不绕过上传扫描；验收允许样本、拒绝样本、超时与错误。

Python span/任务日志用 OTLP/gRPC，配置 OTEL_EXPORTER_OTLP_ENDPOINT=https://端点、OTEL_EXPORTER_OTLP_HEADERS（SDK 格式，如 Authorization=Bearer%20TOKEN）、OTEL_EXPORTER_OTLP_CERTIFICATE=/run/certs/otlp-ca.pem。仅 HTTPS 端点不代表认证通过。Prometheus 私网抓取 API8000、Java8080、研究/资料Worker8001的 /metrics；跨副本数据库状态 gauges 取 max，事件 counters 才求和。完整私网采集、规则、托管备份和送达配置见[监控手册](production-observability.md)；告警模板 observability/alerts.yml，接单失败/积压/心跳/预算/扫描/备份新鲜度与送达须实际核验。默认用户停用的本机备份不能冒充托管恢复能力。

同机 Nginx TLS 入口示例 deployment/nginx/tls-ingress.conf.example，替换域名、证书和上游端口，nginx -t 后加载；证书签发续期由平台负责。云 Compose edge 仅绑定127.0.0.1:APP_PORT，TLS入口/私网LB按部署调整；只暴露工作台入口，Agent/Worker/内部令牌/metrics/数据服务保持私网。覆盖不可信外部转发头，流式接口关闭代理缓冲。

## Compose 应用层

云端编排为独立入口，不与 compose.yaml 混用；不部署本机数据/身份组件，五个应用角色各默认两个副本，edge 与 Agent gateway 分发，无会话粘滞。

```powershell
python scripts/check_production_config.py --env-file .env.cloud --output .cache/cloud-preflight.json
# 外部服务/恢复点准备、审批和迁移验收后，在目标机器启动。
docker compose --project-directory . --env-file .env.cloud -f deployment/compose/compose.cloud.yaml up -d --build --wait --wait-timeout 300
python scripts/probe_external.py --base-url https://你的域名
```

新部署预检要求官方OpenAI嵌入；有历史索引需暂留旧端点时，显式加 --allow-legacy-embedding-endpoint，且旧 EMBEDDING_API_KEY 必须与新 LLM_API_KEY 不同，此例外不授权重建/切换。

预检脱敏检查配置一致、分权、TLS、CSP、共享限额和Worker等，不访问云权限或供应商；production_approved 始终 false。probe_external 不带凭据/模型调用，应从另一台机器调度，并把失败接入真实监控。

本地多副本/故障/负载使用独立 dr-production-test project、私密 .env.production-test 和四个 overlay：compose.yaml、deployment/compose/compose.core.yaml、compose.replicas.yaml、compose.production-test.yaml。最后一个仅受控模型替身，禁止生产使用或指向日常卷/端点。可复用 scripts/production_acceptance.py prepare/smoke/load/faults/all 入口，默认canonical构建并保留源树/镜像/容器基线与报告，详情见[入口契约](production-readiness.md#可复现验收入口)。具体测试与放行以最终报告为准，不把短跑/中断计为30分钟容量通过。

## Kubernetes 应用层

详见 [Kubernetes 操作步骤](../deployment/kubernetes/README.md)。base/resources.json 与独立 migration-job.json 由 scripts/render_kubernetes.py 生成，不手改生成文件；部署参数写自己的 Kustomize overlay。镜像 REPLACE_IMMUTABLE_TAG/所有占位值必须换成实际不可变版本/域名/参数。

- ConfigMap：deepresearch-python/java/web，公开配置与非秘密端点。
- Secret：deepresearch-python、deepresearch-java、deepresearch-migrator，按目录 *.secret.env.example 创建；deepresearch-internal 的 internal_service_token 至少32随机字节；deepresearch-trust 包含所配 CA；deepresearch-ingress-tls 为域名证书。
- NGINX_DNS_RESOLVER 使用真实集群 DNS Service IP（例如 kube-system/kube-dns），不能沿用 Docker 的127.0.0.11。
- Ingress controller namespace 标 deepresearch-ingress=true，监控namespace标 deepresearch-monitoring=true；NetworkPolicy 需有效CNI，基线限制入口，外部出站按实际端点用平台防火墙/私网策略约束。
- 平台准备S3工作负载角色/身份文件与OIDC_CA_CERTS私有信任，service account默认不挂Kubernetes API token；普通应用没有迁移账号。

顺序：创建namespace/Secret/环境overlay → 取得恢复点 → 给独立迁移Job设置同版镜像并运行 → `kubectl wait --for=condition=complete job/deepresearch-migrate --timeout=600s` → 失败停止发布 → 成功再 `kubectl apply -k YOUR_OVERLAY` → 逐一 rollout status → 业务核验。每次发布用新Job名并保留旧证据，不能先一次apply含迁移和应用的资源。

应用默认双副本、滚动maxUnavailable0、PDB保留1，Worker preStop排空、45秒终止宽限包含30秒等待。至少两个可调度节点才可验收节点故障；这些声明不等于生产SLA。集群上仍需登录/权限、停用/交接、Redis降级、滚动升级、真实云数据、负载、告警和隔离恢复验证。

## 最终接入验收与恢复

保存脱敏配置检查、角色/证书失败用例、真实CRUD、OIDC流程、单副本退出、Redis只读、Worker恢复、受限资料权限、异人审核与冻结报告下载结果。完整模型质量需另行费用授权，不能用四次探测替代。容量要求与RPO/RTO实际记录见[生产验收](production-readiness.md#部署与发布验收)。

托管平台配置 PostgreSQL PITR/备份、S3版本/生命周期、IdP配置/数据库及索引快照。独立数据库/Bucket/索引前缀/身份与project恢复，禁模型外发和通知，按身份→数据库/检查点→对象→匹配索引→Redis/Outbox→只读核验→Worker/入口恢复，核对账号权限、引用、正式报告哈希与下载。scripts/recovery.py 只处理单机冷备，不自动恢复托管服务。本轮不重新启用用户停用的本机备份，不自动替换日常容器。

## 外部逻辑恢复工具

`scripts/external_recovery.py` 提供 snapshot/restore/verify，不调用模型、不删除源或恢复资源；当前属于代码交付，真实云/异机恢复仍待验收。运行终端或专用工具镜像必须安装与服务端版本匹配的 pg_dump/pg_restore；精简生产 Python 应用镜像未自带这些客户端，不能直接假定可在应用容器运行。 仓库提供 deployment/docker/Dockerfile.operator：用 --build-arg APP_IMAGE=<本次发布的不可变Python镜像> 构建，内置 PostgreSQL17 客户端并复用应用依赖及已修复SQLite/zlib；该镜像仅用于操作工具，不替代API/Worker镜像。其他PostgreSQL主版本需部署方提供匹配客户端运行环境。私密配置/CA以只读文件挂载，快照目录单独可写；不要将PG管理凭据注入应用容器。

私密 JSON 配置放到 `.env.recovery-source.json` / `.env.recovery-target.json` 等已忽略路径，字段如下（全部占位，真实凭据只在私密文件）：

```json
{
  "settings": {
    "database_url": "postgresql://dr_python:REPLACE@SOURCE_PG/deepresearch?sslmode=verify-full&sslrootcert=/private/postgres-ca.pem",
    "object_store_backend": "s3",
    "object_store_region": "REPLACE_REGION",
    "object_store_bucket": "REPLACE_BUCKET",
    "milvus_uri": "https://REPLACE_MILVUS",
    "milvus_token": "REPLACE",
    "milvus_db_name": "default",
    "vector_collection_prefix": "REPLACE_PREFIX",
    "embedding_model": "text-embedding-3-large",
    "embedding_base_url": "https://api.openai.com/v1",
    "embedding_dimension": 3072,
    "oidc_issuer": "https://REPLACE_IDP/tenant",
    "oidc_jwks_url": "https://REPLACE_IDP/tenant/keys",
    "oidc_audience": "deepresearch-api"
  },
  "pg_admin_url": "postgresql://REPLACE_BACKUP_ROLE:REPLACE@SOURCE_PG/deepresearch?sslmode=verify-full&sslrootcert=/private/postgres-ca.pem",
  "pg_restore_role": "dr_migrator",
  "app_base_url": "https://REPLACE_ISOLATED_APP",
  "tokens_file": "/private/restored-identity-tokens.json",
  "api_ca_certs": "/private/api-ca.pem"
}
```

settings 使用 Settings 的小写字段名；S3凭据/endpoint/SSE、Milvus/OIDC CA 等按前文添加，AWS角色身份也可沿用SDK链。源/目标嵌入模型、端点、维度必须与该恢复点一致，旧代理索引不能套官方默认。目标配置用不同且为空的数据库/Bucket和新集合前缀，IdP原生恢复及数据库角色准备先由平台完成；OIDC没有通用身份平台备份恢复API。

```powershell
# 排空所有研究/资料任务与迁移，冻结写入后；bundle必须为新目录。
python scripts/external_recovery.py snapshot --config .env.recovery-source.json --bundle .env.recovery-bundle --quiesced --report .cache/recovery-snapshot.json
python scripts/external_recovery.py restore --config .env.recovery-target.json --bundle .env.recovery-bundle --isolated --report .cache/recovery-restore.json
# 启动匹配版本的隔离应用/恢复IdP，提供真实签发的测试令牌后核验。
python scripts/external_recovery.py verify --config .env.recovery-target.json --bundle .env.recovery-bundle --isolated --report .cache/recovery-verify.json
```

snapshot 包含 PG custom dump、public表排序行数/SHA、S3原文SHA及Milvus实体，前后SQL摘要检测未排空写入。restore 恢复真实向量，不付费重嵌入，复用分权授权。verify 核对表/对象/向量、真实IdP主体/成员、viewer/停用权限、正式报告快照hash、引用和下载；缺少必要样本不能算通过。私密bundle含完整业务数据，需组织加密/访问控制/异机存放，不能提交Git或公开上传。逻辑工具通过也不替代托管PITR和实际区域级RPO/RTO演练。

协议参考：[OIDC Discovery](https://openid.net/specs/openid-connect-discovery-1_0.html)、[Boto3配置](https://docs.aws.amazon.com/boto3/latest/guide/configuration.html)、[凭据链](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html)、[S3 CopyObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CopyObject.html)、[MilvusClient](https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Client/MilvusClient.md)、[PostgreSQL TLS](https://www.postgresql.org/docs/17/libpq-ssl.html)、[OTLP配置](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/)。实际支持范围以项目实现和目标环境验证为准。
