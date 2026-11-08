# 参与开发

先阅读[项目首页](README.md)、[源码导航](docs/project-structure.md)和[Java／Python 职责](docs/java-python-ownership.md)，在独立分支上完成一个明确问题的修改。

## 开发环境

Python 使用 3.11–3.13 和 `uv`，Java 使用 JDK 21 与 Maven，前端使用与 [CI](.github/workflows/ci.yml) 一致的 Node.js 22。运行集成测试需要 Docker Linux 容器环境；测试使用独立项目和 Testcontainers 依赖。

```powershell
uv sync --all-groups --frozen
npm ci --prefix frontend
```

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

真实模型、嵌入、搜索和重排评测有单独的付费入口与累计账本。执行前确定调用类别、次数和预算；已有账本及失败记录随结果保留。常规回归使用受控替身，详见[模型验证](docs/production-readiness.md#openai-配置与四次真实验证)。

修改评测用例时说明原判定为何不符合预期，并补充正反回归；同义表达修正不放宽无依据事实、引用或拒答约束。保留修改前的原始结果，区分历史输出重新判分与新的真实调用。

## 接口、数据和部署材料

数据库改动新增 Alembic 迁移，保留已执行版本；同步角色授权白名单与旧数据升级验证。共享表新增字段时明确 Java、Python 的写入方。账号、资料与报告变更覆盖权限、并发和重复请求场景。

写入式浏览器、容量、迁移和恢复验证使用独立项目、凭据、端口和数据卷。恢复工具、告警与外部服务配置见[运维手册](docs/deployment-security-operations.md)和[接入手册](docs/cloud-integration.md)。

镜像归档、上游锁和原始安全材料按精确制品身份管理。变更相关源码或构建输入后，按[镜像加载与门禁](docs/approved-image-artifacts.md)重新构建、扫描和审查，保留原始报告及原授权记录。生成文件应修改生成器后重新生成。

## 提交说明

说明触发条件、修改后的行为、验证命令及结果。接口或部署参数变化时同步调用方和文档；证据注明提交 SHA、工作区状态及实际服务版本。安全漏洞通过 [SECURITY.md](SECURITY.md) 中的私有渠道报告。

## 发布前核对

应用版本使用语义化标签，先建立绑定最终提交的 Release 草稿。默认仅发布源码，按[源码发布流程](docs/source-release.md)核对干净副本启动、Git 归档、本地检查、该提交的 CI、安全结果及随源码提供的版权与许可证。第三方镜像按[实际分发要求](THIRD_PARTY_NOTICES.md)单独验收，源码 Release 不代表它们已经全部合规。未完成的检查、条件跳过和许可缺口分别记录。仓库中的 `artifact-*` 标签绑定独立镜像制品，不应移动到应用版本提交；应用发版不自动上传镜像、Packages 或部署生产。

Docker 构建使用独立、LF 检出的干净副本和独立测试配置。Git 忽略规则不控制 Docker 构建上下文；根 `.dockerignore` 是精确镜像审查的绑定输入，修改它需要走对应的重新构建与审查流程，不能直接改写历史授权或哈希。私密日志、恢复包和验收输出放在已排除的 `.cache/`，个人资料放在 `.local/personal/`；不要把其他私密文件放进构建目录。验证发布前，同时检查已跟踪文件、未跟踪文件和 Docker 实际 COPY 范围。
