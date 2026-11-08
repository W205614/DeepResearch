# DeepResearch 研究工作台

输入研究问题，从公开网页或授权资料中查找依据，生成带引用的中文报告。支持团队协作、资料权限、异人审核、正式报告冻结与修订，技术栈为 **Spring Boot + LangGraph + Vue 3**。

个人本地版本的主要功能与交付验证已完成，可以暂停功能扩建，按需启动和维护。企业内部单区域多副本部署也已提供客户端、配置和部署模板，接入步骤见[云平台手册](docs/cloud-integration.md)。

**2026-11-08 日常维护验证**补充了真实 OpenAI 回答、联网研究、官方嵌入与 SiliconFlow 重排，并验证本地资料到引用回答的完整链路。修复包括迁移计数序列化、报告遗漏具体局限、任务收尾被心跳误取消，以及长网页正文截断。各阶段结果、评测用例修正和验证边界见[本轮记录](docs/evidence/daily-20261108/README.md)，不把请求次数换算为通用准确率。

此前的隔离组件、权限、故障恢复和混合负载验证见[证据索引](docs/evidence/production-20261107/README.md)；其中负载测试使用受控模型替身，不代表真实供应商容量。本机已有 118 个历史分块完成官方嵌入的影子索引迁移，配置和原始业务数据保留。第三方制品的分发验收仍有待完成，应用候选保持 Release 草稿；不因此阻塞个人本机按需使用。

- 运行：[本地快速部署](#本地快速部署)。
- 接入：[云平台手册](docs/cloud-integration.md) · [生产验收](docs/production-readiness.md)。
- 开发：[源码导航](docs/project-structure.md) · [参与开发](CONTRIBUTING.md) · [安全反馈](SECURITY.md)。

## 功能

| 方向 | 能力 |
| --- | --- |
| 研究 | 自动判断、快速问答、深度研究、范围确认、同会话追问、活动进度、停止与恢复 |
| 资料 | TXT、Markdown、DOCX、PDF、图片；扫描隔离、解析限制、向量与词法融合检索、集合授权与撤权 |
| 证据 | 引用原文、文档版本与差异、影响分析、草稿下载、正式报告冻结、异人审核与修订 |
| 模型 | OpenAI 对话、视觉、联网搜索与官方嵌入；BGE／SiliconFlow 重排 |
| 治理 | 账号停用与恢复、旧会话撤销、空间交接、取消任务、用量与审计管理页 |
| 运行 | Redis 共享限流、PostgreSQL 任务与预算、Outbox、跨副本准入、检查点、Worker 心跳与排空 |
| 接入 | OIDC、TLS PostgreSQL／Redis／Milvus、S3 临时凭据与工作负载身份、OTLP、Compose、Kubernetes |

默认全平台最多 5 个执行任务、20 个等待任务；单空间最多 2 个执行任务、500 份资料、5 万片段。支持的外部协议可通过端点、凭据、证书和部署参数接入。目标云平台的资源、IAM、MFA／SMTP、备份和告警渠道由部署方配置；接入后按[验收清单](docs/production-readiness.md#部署与发布验收)验证。

## 本地快速部署

需要 Git、Docker、Docker Compose 和 Python 3.11+。Java 与前端由 Docker 构建。固定镜像使用 Linux/amd64，Windows 使用 Docker Desktop 的 Linux 容器。

在 PowerShell 中运行：

```powershell
git clone --config core.autocrlf=false https://github.com/W205614/DeepResearch.git
cd DeepResearch
Copy-Item .env.example .env
```

LF 检出配置用于保持精确构建输入字节；原始证据按各自 Git 属性保留。已有安装保留原 `.env` 和数据卷，升级前准备匹配的恢复点，步骤见[部署与维护](docs/deployment-security-operations.md#现有数据升级)。

填写本地私密 `.env`：

| 配置 | 说明 |
| --- | --- |
| `LLM_API_KEY` | OpenAI 项目密钥；默认 `gpt-6.1-sol`、`low`，官方端点 |
| `EMBEDDING_API_KEY` | 新安装使用同一 OpenAI 项目密钥；`text-embedding-3-large`、3072 维 |
| `RERANKER_API_KEY` | SiliconFlow 凭据；关闭可选重排时设 `RERANKER_ENABLED=false` |
| `POSTGRES_PASSWORD`、`KEYCLOAK_DB_PASSWORD`、`KEYCLOAK_ADMIN_PASSWORD` | 应用库、身份库和身份管理台的独立密码 |
| `MINIO_ROOT_PASSWORD`、`GRAFANA_ADMIN_PASSWORD` | 存储与观测管理台的独立密码 |
| `MIGRATOR_DB_PASSWORD`、`JAVA_DB_PASSWORD`、`PYTHON_DB_PASSWORD`、`MONITOR_DB_PASSWORD` | 迁移、Java、Python、监控分权账号 |
| `OBJECT_STORE_ACCESS_KEY`、`OBJECT_STORE_SECRET_KEY`、`MILVUS_STORAGE_PASSWORD` | 资料与向量存储的独立服务身份 |

`python scripts/prepare_service_credentials.py --env .env` 可补齐空的服务凭据，并备份原配置。`PLATFORM_OPERATOR_SUBJECTS` 填 IdP 账号的稳定 `sub`，多个值用逗号分隔。

**已有历史索引时，先保留原 `EMBEDDING_*` 配置和凭据。** 向量指纹包含端点、模型和维度，切换官方嵌入前需要完成[影子索引迁移](docs/production-readiness.md#历史索引迁移与回退)。OpenAI 密钥只用于官方端点，旧第三方端点继续使用其原凭据。

加载批准镜像并启动：

```powershell
python scripts/fetch_approved_image.py
python scripts/fetch_grafana_fixed_20261107.py
docker compose up -d --build --wait --wait-timeout 300
```

加载器核对归档、OCI 描述符、层、配置和二进制后载入 Docker。离线加载使用 `--archive <归档路径>`。资源有限时可关闭可选观测栈：

```powershell
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml up -d --build --wait --wait-timeout 300
```

| 入口 | 默认地址 | 登录 |
| --- | --- | --- |
| 工作台 | http://localhost:8080 | 本地 IdP 账号 |
| Keycloak 管理台 | http://localhost:8180/admin | `admin` / `KEYCLOAK_ADMIN_PASSWORD` |
| Grafana | http://localhost:3000 | `admin` / `GRAFANA_ADMIN_PASSWORD` |

本地默认开放注册；云模板关闭注册入口，组织账号策略在 IdP 中配置。Keycloak 管理员、平台操作员与空间管理员分别管理不同权限。

## 完成一次研究

1. 登录并创建个人空间，选择“公开问题（联网检索）”，输入问题，开始研究。
2. 查看活动进度，完成后打开引用与原文片段，导出 Markdown。
3. 使用内部资料时，先确认模型服务的数据处理范围，再设 `ALLOW_INTERNAL_MODEL_PROCESSING=true` 并重启使用方。上传资料，扫描和索引完成后选择“内部资料（不联网）”。该模式关闭公开搜索，配置的模型服务仍会处理资料。
4. 按需设置资料集合授权。撤权会同步影响历史正文、记忆、来源和下载。
5. 作者提交正式报告，由另一位有权限管理员审核。批准后冻结正文和证据；后续修订生成新版本，保留历史作者。

单文件上限 10 MB，每轮最多 4 张图片。公开模式使用公开来源；证据不足会保留部分结果并列出缺口。供应商调用按持久化调用与 Token 预算预占，预算耗尽后停止下一次外发。

## 管理、部署与恢复

平台管理页提供账号、空间交接、任务取消、运行状态、用量和审计。停用前完成空间交接；最后一名有效管理员受到保护。恢复账号后重新登录，旧会话仍失效。接口与字段见[治理契约](docs/production-readiness.md#账号治理和管理契约)。

Redis 不可用时，已建立账号仍可读取有权限的历史，新研究、上传和其他业务修改返回暂不可用；数据库与 Outbox 保存接单和恢复状态。Worker 支持心跳、就绪检查与有界排空。

- 云平台：[配置、证书、身份与迁移](docs/cloud-integration.md)。
- 多副本：[应用 Compose](deployment/compose/compose.cloud.yaml) · [Kubernetes](deployment/kubernetes/README.md)。
- 验收：[隔离负载、故障与恢复入口](docs/production-readiness.md#可复现验收入口)。
- 运维：[备份与升级](docs/deployment-security-operations.md) · [监控与告警](docs/production-observability.md)。

日常暂停使用 `docker compose stop`，保留容器和数据；`docker compose down` 删除容器但保留命名卷。`down -v` 会删除卷，不用于日常停止。已有 Compose 覆盖文件和项目名应保持一致。自动备份按本机停用策略保留；升级时手动准备并验证恢复点。完整模型质量、目标云环境权限／TLS、告警送达和异机恢复按各自手册验收。

## 按需维护

恢复开发前读取当前 `main`、检查配置和待处理的安全报告，按[贡献指南](CONTRIBUTING.md)运行直接相关的检查。依赖、镜像、构建输入或扫描数据库更新时，按[镜像维护流程](docs/approved-image-artifacts.md)重新验证。真实模型评测、历史向量重建和数据清理分别使用明确的调用预算与操作范围。

[工作台](docs/images/workspace.png) · [报告与证据](docs/images/research-report.png) · [正式审核](docs/images/report-review.png)是固定数据的界面示例。

| 主题 | 文档 |
| --- | --- |
| 开发 | [贡献指南](CONTRIBUTING.md) · [目录与调用路径](docs/project-structure.md) · [Java／Python 职责](docs/java-python-ownership.md) |
| 使用 | [登录](docs/local-keycloak-login.md) · [多模态](docs/multimodal.md) · [资料与报告生命周期](docs/evidence-lifecycle.md) |
| 验收 | [生产验收](docs/production-readiness.md) · [真实 API 与日常验收](docs/evidence/daily-20261108/README.md) · [本地证据索引](docs/evidence/production-20261107/README.md) |
| 历史 | [交付记录](docs/delivery-history.md) · [检索评测](eval/benchmark_results.md) · [安全维护记录](docs/security-maintenance-20261105.md) |

## 许可证

独立原创代码和文档采用 [MIT License](LICENSE)，Copyright (c) 2026 W205614。第三方源码、字体、依赖和容器组件保留各自许可证，范围与来源见[第三方说明](THIRD_PARTY_NOTICES.md)。其中 Grafana、Loki、Tempo 和 MinIO／Silo 涉及 AGPL；其派生程序不改为 MIT。第三方二进制的完整对应源码与分发义务仍需按目标制品逐项验收，详见该说明中的发布前检查项。
