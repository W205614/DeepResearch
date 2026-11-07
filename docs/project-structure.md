# 项目结构与本地文件

首次部署与使用见 [README](../README.md)。本文说明现行目录职责和整理边界。

| 目录 | 职责 |
| --- | --- |
| `business-backend/` | Java 业务控制面、身份权限、工作空间、任务与审核发布 |
| `backend/` | Python 研究编排、检索、文档处理与 Worker |
| `frontend/` | Vue 工作台及前端依赖、测试与构建配置 |
| `migrations/` | Alembic 数据库迁移；保留既有迁移及版本顺序 |
| `keycloak/` | 身份服务的公共配置与初始化材料 |
| `observability/` | Prometheus、Grafana、日志与追踪配置 |
| `scripts/` | 开发、运维、备份恢复、验证与评测工具；按需阅读，不批量运行 |
| `tests/` | Python 回归测试；运行前区分离线、组件与外部服务场景 |
| `eval/` | 固定评测集、来源、策略、评分与历史验证证据 |
| `examples/` | 示例材料；核对文档和测试引用后再调整 |
| `deployment/compose/` | 开发、资源受限、验收、组件验证、性能与告警测试编排 |
| `deployment/cloud.env.example`、`deployment/postgres/`、`deployment/nginx/` | 外部服务配置模板、专用云数据库初始化与 TLS 入口示例；按[接入指南](production-readiness.md)使用 |
| `deployment/docker/` | 未绑定固定审查路径的基础组件与验证镜像构建配方 |
| `deployment/vendors/`、`deployment/security/` | 上游依赖、锁定来源、安全审查及绑定证据 |
| `docs/` | 使用说明、职责边界、运维与历史交付证据 |
| `.github/workflows/` | CI、安全扫描和模型评测入口 |

## 一条研究请求的调用路径

以工作台发起一条公开研究为例，按请求实际经过的顺序阅读。

1. **提交问题**　[App.vue](../frontend/src/App.vue) 先请求 `POST /api/research/intakes` 判断是否需要确认范围。无需确认时直接提交 `POST /api/research/runs`。
2. **身份与任务**　[ResearchController](../business-backend/src/main/java/com/deepresearch/business/api/ResearchController.java) 接收请求，通过 [WorkspaceAccess](../business-backend/src/main/java/com/deepresearch/business/security/WorkspaceAccess.java) 解析工作空间身份。[ResearchCommandService](../business-backend/src/main/java/com/deepresearch/business/service/ResearchCommandService.java) 创建任务，并写入 `research.schedule` 待发命令。
3. **调度与执行**　[OutboxService](../business-backend/src/main/java/com/deepresearch/business/service/OutboxService.java) 投递命令，[Python 内部 API](../backend/api/app.py) 接受调度。[Worker](../backend/worker.py) 调用 [Runtime.execute_run](../backend/services/runtime.py) 执行[研究图](../backend/research/graph.py)。调整公开检索策略看 [search_policy.py](../backend/research/search_policy.py)，调整内部资料检索看 [documents.py](../backend/services/documents.py)。
4. **进度与报告**　[前端 API](../frontend/src/api.ts) 按事件游标接收进度；工作台加载报告并处理引用点击，[ReportReader.vue](../frontend/src/ReportReader.vue) 负责报告阅读布局。

## 常用脚本

从仓库根目录使用。先按任务选入口，再核对其运行前提。

| 任务 | 入口与效果 |
| --- | --- |
| 部署与迁移 | [prepare_service_credentials.py](../scripts/prepare_service_credentials.py) 补齐本机配置中的服务凭据；Compose 的迁移作业调用 [migrate_services.py](../scripts/migrate_services.py)，执行 Alembic、索引回填和数据库授权。 |
| 评测 | [回答质量工作流](../.github/workflows/answer-quality.yml) 调用 [evaluate_research.py](../scripts/evaluate_research.py)；[检索质量工作流](../.github/workflows/rag-quality.yml) 默认只准备环境。真实供应商评测会产生费用。 |
| 压测 | [load-health.js](../scripts/load-health.js) 是 k6 健康端点负载场景；通过 `BASE_URL` 选择目标，`VUS` 和 `DURATION` 控制负载。 |
| 运维：本地备份轮换 | 从根目录手动运行 [install-backup-task.ps1](../scripts/install-backup-task.ps1)，无必填参数。它注册或覆盖 Windows 任务 `DeepResearchDailyBackup`，默认 02:00 调用 [backup-retention.ps1](../scripts/backup-retention.ps1)，保留最近七份并清理过期备份。 |
| 运维：校验冷备与容量探测 | [运维手册](deployment-security-operations.md#配置调度) 使用 [install_backup_task.ps1](../scripts/install_backup_task.ps1)，必填 `-Configuration` 私密配置。它在宿主机注册 `DeepResearch-Verified-Cold-Backup` 与 `DeepResearch-Host-Probe` 两个任务，分别调用 [scheduled_backup.py](../scripts/scheduled_backup.py) 和 [ops_probe.py](../scripts/ops_probe.py)。安装这两类调度均应在明确选择备份策略后进行。 |
| 安全审查 | [fetch_approved_image.py](../scripts/fetch_approved_image.py) 和 [fetch_grafana_candidate.py](../scripts/fetch_grafana_candidate.py) 按批准记录验证镜像，成功后载入本机 Docker；首次部署的参数见[快速部署](../README.md#快速部署)。 |

## 根目录入口

- `pyproject.toml` 与 `uv.lock` 分别声明和锁定 Python 依赖，应配套保留。
- `main.py` 是 Python 入口；`alembic.ini` 配合 `migrations/` 使用。
- `compose.yaml` 是默认部署编排；`compose.production.yaml` 与当前本机覆盖文件的路径已记录在现有容器中，因此保留在根目录，保证停止环境仍可按原入口恢复。
- `deployment/compose/` 按运行场景保存覆盖配置和独立编排。覆盖文件以根目录 `compose.yaml` 为第一份配置；`compose.cloud.yaml` 是独立的外部服务应用编排，不叠加根目录配置。单独使用独立编排时，显式指定 `--project-directory .`，保持构建上下文和挂载路径以仓库根目录为基准。
- `Dockerfile` 是应用构建入口；`Dockerfile.minio`、`Dockerfile.grafana` 和 `Dockerfile.milvus` 按原路径及文件哈希参与安全证据绑定，原位保留。`Dockerfile.postgres` 保留既有数据库构建入口。
- 组件配方按服务命名，例如 `deployment/docker/Dockerfile.tempo`，可从相应 Compose 服务的 build 配置追到配方；构建上下文仍是仓库根目录。
- `.env.example`、`.dockerignore`、`.gitattributes`、`.gitignore` 保留在根目录。
- Java 的 `pom.xml` 在 `business-backend/`；前端的 `package.json` 和 `package-lock.json` 在 `frontend/`，不移动到根目录或删除锁文件。

## 本机文件

| 路径 | 整理原则 |
| --- | --- |
| `.env`、`.env.local-nginx.conf`、`.env.local-ports.compose.yaml`、`.env.local-networks.compose.yaml` | 本机私密或端口／代理配置，保持 Git 忽略；当前部署依赖这些路径，原位保留 |
| `.local/personal/` | 私人学习与求职资料，整组保留；Git 与 Docker 构建上下文均忽略此目录，本地恢复映射与 SHA-256 清单也在其中 |
| `data/` | 本机运行数据、资料与索引；不作为缓存批量清理 |
| `.cache/` | 可能包含评测账本、备份、回滚包及验收证据；不能整目录删除 |
| `.taste-skill/`、`.idea/` | 本机设计与 IDE 资料，保持本地忽略 |
| `.venv/`、`node_modules/`、`target/`、`dist/`、各类工具缓存 | 可生成但可能仍被任务使用；结构整理不默认清空 |

个人资料整组移动时保留文件内容及互相引用。资料内写明的源码路径和命令仍以仓库根目录为基准，不代表程序需要这些私人文件。

## 编排命令

以下命令从仓库根目录运行；`config --quiet` 只解析配置，不启动服务。

```sh
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml config --quiet
docker compose -f compose.yaml -f deployment/compose/compose.development.yaml config --quiet
docker compose --project-directory . -f deployment/compose/compose.verify.yaml config --quiet
docker compose --project-directory . -f deployment/compose/compose.verify.yaml -f deployment/compose/compose.performance.yaml config --quiet
```

## 手写源码格式

Vue 和 TypeScript 使用两空格缩进，Java 和 Python 使用四空格缩进；根目录的 [.editorconfig](../.editorconfig) 将设置限定在应用源码路径。Python 格式试点为 `backend/api/`，从仓库根目录运行 `uv run --frozen ruff format --check backend/api`，与 CI 的检查范围一致。固定语料、安全证据和上游快照按 [.gitattributes](../.gitattributes) 保留原始字节。

## 整理与验证

历史记录从 [交付索引](delivery-history.md) 查询。报告与截图只证明各自提交和场景下的结果，不能仅因日期较早就删除。

只调整文档和忽略规则时，检查 Git 差异、文档本地链接、私人文件的忽略状态及移动前后哈希；不需要为此启动服务。结构或入口发生变化时，再按影响范围选择离线检查。

真实模型评测是独立流程，可能产生供应商费用；不要把 `evaluate_*.py`、模型质量工作流或容器验证脚本当作普通清理检查批量执行。
