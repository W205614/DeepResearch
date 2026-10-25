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
| `docs/` | 使用说明、职责边界、运维与历史交付证据 |
| `.github/workflows/` | CI、安全扫描和模型评测入口 |

## 根目录入口

- `pyproject.toml` 与 `uv.lock` 分别声明和锁定 Python 依赖，应配套保留。
- `main.py` 是 Python 入口；`alembic.ini` 配合 `migrations/` 使用。
- `compose.yaml` 是默认部署编排；`compose.acceptance.yaml`、`compose.performance.yaml`、`compose.test.yaml`、`compose.verify.yaml` 对应不同验证场景，不是可按名称去重的副本。
- `Dockerfile`、`Dockerfile.minio`、`Dockerfile.verify` 分别服务应用、对象存储和验证镜像。
- `.env.example`、`.dockerignore`、`.gitattributes`、`.gitignore` 保留在根目录。
- Java 的 `pom.xml` 在 `business-backend/`；前端的 `package.json` 和 `package-lock.json` 在 `frontend/`，不移动到根目录或删除锁文件。

## 本机文件

| 路径 | 整理原则 |
| --- | --- |
| `.env`、`.env.local-nginx.conf`、`.env.local-ports.compose.yaml` | 本机私密或端口／代理配置，保持 Git 忽略；当前部署可能依赖它们，不按临时文件处理 |
| `.local/personal/` | 私人学习与求职资料，整组保留；Git 与 Docker 构建上下文均忽略此目录，本地恢复映射与 SHA-256 清单也在其中 |
| `data/` | 本机运行数据、资料与索引；不作为缓存批量清理 |
| `.cache/` | 可能包含评测账本、备份、回滚包及验收证据；不能整目录删除 |
| `.taste-skill/`、`.idea/` | 本机设计与 IDE 资料，保持本地忽略 |
| `.venv/`、`node_modules/`、`target/`、`dist/`、各类工具缓存 | 可生成但可能仍被任务使用；结构整理不默认清空 |

个人资料整组移动时保留文件内容及互相引用。资料内写明的源码路径和命令仍以仓库根目录为基准，不代表程序需要这些私人文件。

## 整理与验证

历史记录从 [交付索引](delivery-history.md) 查询。报告与截图只证明各自提交和场景下的结果，不能仅因日期较早就删除。

只调整文档和忽略规则时，检查 Git 差异、文档本地链接、私人文件的忽略状态及移动前后哈希；不需要为此启动服务。结构或入口发生变化时，再按影响范围选择离线检查。

真实模型评测是独立流程，可能产生供应商费用；不要把 `evaluate_*.py`、模型质量工作流或容器验证脚本当作普通清理检查批量执行。
