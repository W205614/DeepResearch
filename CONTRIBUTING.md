# 参与开发

先阅读[项目首页](README.md)、[源码导航](docs/project-structure.md)和[Java／Python 职责](docs/java-python-ownership.md)，在独立分支上完成一个明确问题的修改。

## 开发环境

Python 使用 3.11–3.13 和 `uv`，Java 使用 JDK 21 与 Maven，前端使用与 [CI](.github/workflows/ci.yml) 一致的 Node.js 22。运行集成测试需要 Docker Linux 容器环境；测试使用独立项目和 Testcontainers 依赖。

```powershell
uv sync --all-groups --frozen
npm ci --prefix frontend
```

完整应用开发可先在独立克隆和私密配置中沿用 Docker 编排。`main` 是开发分支，已发布源码 Tag 和镜像分发 Tag 的来源见[版本表](README.md#版本镜像与安全状态)。Python 容器热更新使用已有覆盖：

```powershell
git clone --config core.autocrlf=false --branch main https://github.com/W205614/DeepResearch.git DeepResearch-dev
cd DeepResearch-dev
python scripts/local.py init
# 填写该独立目录的私密 .env，再按机器实际选择端口覆盖。
python scripts/local.py check --project-name deepresearch-dev --compose-file deployment/compose/compose.development.yaml
python scripts/local.py start --project-name deepresearch-dev --compose-file deployment/compose/compose.development.yaml
# 暂停时保留相同参数与数据。
python scripts/local.py stop --project-name deepresearch-dev --compose-file deployment/compose/compose.development.yaml
```

覆盖文件只把 Python `backend/` 只读挂入 Agent／Worker，并让 Agent 使用 `--reload`；Java 和 Web 仍按正常 Docker 配方构建。新项目名不能避免宿主端口冲突，端口覆盖需显式追加，启动与停止保持同一顺序。

需要观测组件时，启动和停止都追加 `--observability`。当前工程入口会先准备 MinIO，观测栈再调用固定 Grafana 源码 builder，得到本地 `deepresearch-grafana-source:20261112`；`--no-build` 仍要校验已有镜像和构建收据，不能用下载旧输入代替更新成功。新 Grafana 没有作为公开第三方 Packages 分发，具体依赖与命令见[源码启动](docs/source-release.md#运行依赖如何获取)。

宿主 Python、Maven 和 npm 可独立运行下节检查。完整服务的宿主混合启动没有一键编排入口：迁移、OIDC、角色账号、Redis、Milvus、对象存储、ClamAV 及网络端点都需按源码契约准备。当前 Vite `/api` 代理直接指向 Python `127.0.0.1:8000`，不能把 `npm run dev` 当作完整 Java 业务入口的已验收替代；需要完整权限与审核流程时使用上述编排。不要让开发进程指向日常数据库。

本地凭据使用 Git 忽略的私密配置。提交日志、截图或复现材料前移除访问令牌、密钥、真实资料与账号信息。新增依赖应说明官方来源、许可、维护情况和集成成本；第三方源码与资产保留原版权及许可证。

## 修改与验证

复用现有鉴权、事务、异常和任务状态机。先验证可复现问题，再测试真实实现与直接调用方。仅修改文档时检查链接、命令与格式；业务改动选择关联测试，例如：

```powershell
uv run ruff check backend tests scripts main.py
uv run pytest -q tests/test_consistency.py

# Java 改动；集成测试会使用独立 PostgreSQL 容器
mvn -B test -f business-backend/pom.xml

# 前端改动
npm test --prefix frontend -- --run
npm run build --prefix frontend
```

完整自动检查由 [CI](.github/workflows/ci.yml)、[Security](.github/workflows/security.yml) 和[告警规则工作流](.github/workflows/observability.yml)执行。提交后检查对应提交的结果，记录条件跳过和仍需目标环境验证的项目。

历史正式 `v0.1.3` 工程提交 `78a19df9dfa6f072af6ebe40ff99cf164d0c4ff8` 的 [CI](https://github.com/W205614/DeepResearch/actions/runs/42968387539)4 项与 [Security](https://github.com/W205614/DeepResearch/actions/runs/42968387608)22 项成功，[三个 Packages](https://github.com/W205614/DeepResearch/actions/runs/42977626649)发布、实际匿名拉取和日常验收均完成。四个隔离浏览器用例及资料研究引用检查通过，模型使用固定夹具；其 Windows TCP relay 与日常普通端口验收分别记录。另复用 2026-11-10 从同一未变化 Agent 制品执行的两次官方 API 契约探针（JSON、3072 维嵌入，0 重试、原预算上限 1 USD）；本次没有追加调用，不为文档或重测重置账本。版本与组件来源见[镜像说明](docs/container-packages.md)。

当前正式 `v0.1.4` 的来源、发布时 CI 豁免及未完成拉取／运行验收边界见[镜像说明](docs/container-packages.md#正式-v014)。后续 CI 修复结果必须绑定其实际新提交，不改写旧版本的失败。

真实模型、嵌入、搜索和重排评测有单独的付费入口与累计账本。执行前确定调用类别、次数和预算；已有账本及失败记录随结果保留。常规回归使用受控替身，详见[模型验证](docs/production-readiness.md#openai-配置与四次真实验证)。

修改评测用例时说明原判定为何不符合预期，并补充正反回归；同义表达修正不放宽无依据事实、引用或拒答约束。保留修改前的原始结果，区分历史输出重新判分与新的真实调用。

## 接口、数据和部署材料

数据库改动新增 Alembic 迁移，保留已执行版本；同步角色授权白名单与旧数据升级验证。共享表新增字段时明确 Java、Python 的写入方。账号、资料与报告变更覆盖权限、并发和重复请求场景。

写入式浏览器、容量、迁移和恢复验证使用独立项目、凭据、端口和数据卷。恢复工具、告警与外部服务配置见[运维手册](docs/deployment-security-operations.md)和[接入手册](docs/cloud-integration.md)。

镜像归档、上游锁和原始安全材料按精确制品身份管理。变更相关源码或构建输入后，按[镜像加载与门禁](docs/approved-image-artifacts.md)重新构建、扫描和审查，保留原始报告及原授权记录。生成文件应修改生成器后重新生成。

## 提交说明

说明触发条件、修改后的行为、验证命令及结果。接口或部署参数变化时同步调用方和文档；证据注明提交 SHA、工作区状态及实际服务版本。安全漏洞通过 [SECURITY.md](SECURITY.md) 中的私有渠道报告。

## 发布前核对

应用交付采用 Docker/OCI 镜像；Maven 保留源码编译、测试和镜像内 JAR，不执行 `mvn deploy`。源码版本按[源码流程](docs/source-release.md)核对归档、检查和 CI，应用镜像按[Packages 流程](docs/container-packages.md)完成实际候选、许可材料、匿名拉取、运行与适用日常验收。产品版本集中说明源码与镜像对应关系；测试轮次不新建版本，不覆盖公开 Tag／附件／镜像。现有应用制品和独立第三方 `artifact-*` 保留其实际来源与材料职责，不能只因名称删去获取入口。常规应用发版不自动上传 Packages 或部署生产；显式手动工作流的输入和权限须先核对。未完成检查、条件跳过和许可缺口分别记录。

Docker 构建使用独立、LF 检出的干净副本和独立测试配置。Git 忽略规则不控制 Docker 构建上下文；根 `.dockerignore` 是精确镜像审查的绑定输入，修改它需要走对应的重新构建与审查流程，不能直接改写历史授权或哈希。私密日志、恢复包和验收输出放在已排除的 `.cache/`，个人资料放在 `.local/personal/`；不要把其他私密文件放进构建目录。验证发布前，同时检查已跟踪文件、未跟踪文件和 Docker 实际 COPY 范围。
