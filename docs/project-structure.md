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
| `deployment/docker/` | 未绑定固定审查路径的基础组件与验证镜像构建配方 |
| `deployment/vendors/`、`deployment/security/` | 上游依赖、锁定来源、安全审查及绑定证据 |
| `docs/` | 使用说明、职责边界、运维与历史交付证据 |
| `.github/workflows/` | CI、安全扫描和模型评测入口 |

## 根目录入口

- `pyproject.toml` 与 `uv.lock` 分别声明和锁定 Python 依赖，应配套保留。
- `main.py` 是 Python 入口；`alembic.ini` 配合 `migrations/` 使用。
- `compose.yaml` 是默认部署编排；`compose.production.yaml` 与当前本机覆盖文件的路径已记录在现有容器中，因此保留在根目录，保证停止环境仍可按原入口恢复。
- `deployment/compose/` 保存 6 个不同场景的编排，不是重复备份。覆盖文件以根目录 `compose.yaml` 为第一份配置；单独使用组件验证编排时，显式指定 `--project-directory .`，保持构建上下文和挂载路径以仓库根目录为基准。
- `Dockerfile` 是应用构建入口；`Dockerfile.minio`、`Dockerfile.grafana` 和 `Dockerfile.milvus` 按原路径及文件哈希参与安全证据绑定，原位保留。`Dockerfile.postgres` 保留既有数据库构建入口。
- 其余 5 个配方归入 `deployment/docker/`，Compose、脚本、工作流和使用说明同步引用新路径；构建上下文仍是仓库根目录。
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

## 本地备份保留

2026-10-28 整理时，最新完整恢复快照为 `.cache/backup/scheduled/scheduled-20261027-042116/`，创建于北京时间 2026-10-27 12:27:48。已重新检查 39 个清单文件的 SHA-256、冻结配置、镜像归档及 OCI 镜像层；保留原有成功恢复报告。本次未重新启动恢复环境。该快照是当时的完整状态，不能当作 2026-10-28 部署的新全量备份。

较新的数据库逻辑备份位于 `.cache/cleanup-current-20261028/probes/database-backups/`，与完整快照用途不同，一并保留。7 份更旧全量备份移入 Windows 回收站；当前私密配置、备份调度状态、独有计费账本和现行部署恢复材料保留。完整回收清单与核验记录见本机 `.cache/structure-cleanup-20261028/`。

备份调度入口仍为 `.cache/backup/scheduled/`；计划任务还依赖 `.cache/optimization-20261027-daily/` 内的配置，不整体搬移或清空这些目录。后续只在新完整备份通过校验后替换旧快照；不同用途的逻辑备份、运行恢复包及审计材料不能仅按日期去重。

## 整理与验证

历史记录从 [交付索引](delivery-history.md) 查询。报告与截图只证明各自提交和场景下的结果，不能仅因日期较早就删除。

只调整文档和忽略规则时，检查 Git 差异、文档本地链接、私人文件的忽略状态及移动前后哈希；不需要为此启动服务。结构或入口发生变化时，再按影响范围选择离线检查。

真实模型评测是独立流程，可能产生供应商费用；不要把 `evaluate_*.py`、模型质量工作流或容器验证脚本当作普通清理检查批量执行。
