# 源码发布与本地运行

应用 `v*` 标签发布源码和版本说明；`artifact-*` 标签绑定独立第三方镜像。两者可以引用不同提交：应用变更没有修改第三方构建输入时，继续使用已验证镜像，不移动历史标签或重复上传同一镜像。

## 克隆后启动

需要 Git、Python 3.11+ 和 Linux/amd64 Docker Engine（含 Compose v2）。Windows 使用 Docker Desktop 的 Linux 容器。Java、Node.js 与应用依赖由 Docker 构建；首次构建仍需下载上游镜像、源码和依赖，不能视为离线安装。

```powershell
git clone --config core.autocrlf=false --branch v0.1.0 https://github.com/W205614/DeepResearch.git
cd DeepResearch
python scripts/local.py init
```

初始化只在 `.env` 不存在时创建配置，生成独立的数据库、存储和管理端密码，不输出其值。已有 `.env` 原样保留；生成器的原配置副本 `.env.before-service-roles` 也只留在本机。填写 `LLM_API_KEY`、`EMBEDDING_API_KEY`；使用重排时填写 `RERANKER_API_KEY`，否则明确设置 `RERANKER_ENABLED=false`。

```powershell
python scripts/local.py check
python scripts/local.py start
# 默认核心服务。暂停后保留容器、配置和数据。
python scripts/local.py stop
```

工作台默认 `http://localhost:8080`，Keycloak 管理台 `http://localhost:8180/admin`，管理员为 `admin`，密码从私密 `.env` 的 `KEYCLOAK_ADMIN_PASSWORD` 读取。工作台使用本地 IdP 注册的业务账号，不能拿 Keycloak 管理台账号直接当业务账号。

完整观测栈使用 `start --observability`，停止时同样加 `--observability`。`--project-name`、`--env-file` 和重复的 `--compose-file` 用于明确指定项目与覆盖配置，启动和停止须使用同一套参数。已有日常部署继续保留原项目名、端口覆盖和 Compose 文件，不要把首次安装命令直接用于替换现有环境。

`check` 只核对必需配置、Docker 平台与 Compose 解析，不验证 API Key 是否有效，也不发出模型请求。`start` 默认构建；`--no-build` 仅用于已有匹配镜像的环境，不代表本次重新构建过。

## 运行依赖如何获取

| 内容 | 来源与版本依据 |
| --- | --- |
| Java、Python、前端及其他带 `build` 的服务 | 从所检出标签中的 Dockerfile 和锁文件构建 |
| 核心 MinIO／Silo | `fetch_approved_image.py` 获取固定 `artifact-minio-fixed-20261026`，按绑定记录校验后加载；默认启动需要它 |
| 可选 Grafana | `--observability` 才加载 `artifact-grafana-fixed-20261107`；[完整版本与校验说明](approved-image-artifacts.md) |
| Redis、ClamAV 等上游镜像 | 使用 `compose.yaml` 中固定的版本与摘要 |

源码 Release 不附预构建容器，也不代表上述第三方制品已经重新构建或完成所有分发验收。下载器仍依赖既有镜像 Release 可访问；不要删除附件或改变摘要。当前 Grafana 构建还复用旧批准镜像中的资产，并非完全脱离历史镜像。第三方许可和既有制品待验事项继续见 [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)，本流程不豁免这些义务。

## 维护者发布步骤

1. 检查本次差异、许可证和待发布源码内容，不携带 `.env`、业务数据、恢复包或本地证据。源码与第三方镜像分别按实际分发内容核查。
2. 提交并推送；等待该提交的 CI、Security 和适用的观测规则检查完成。CI 的源码归档检查不等同于镜像许可检查。
3. 从最终提交的干净副本执行 `init`、`check`、构建启动和核心回归，记录复用的 Docker 缓存、外部镜像和未运行项目。停止隔离测试栈；不要操作既有业务卷。
4. 对最终提交运行 `python scripts/verify_source_release.py --ref HEAD --output .cache/source-release`。它核对 Git 归档内的必要文件、私密路径和符号链接，并输出源码逐文件摘要。另行检查内容中的真实凭据，路径检查不能代替秘密扫描。
5. 为同一提交创建新的语义化标签，不覆盖旧标签。先建立 Release 草稿，核对目标、变更、安装与已知限制，再正式发布。默认只使用 GitHub 自动生成的源码归档；`.cache/source-release` 是本地核对材料，不自动上传。

现有 CI 只构建和验证，不因应用标签或 Release 自动推送镜像或部署生产；Grafana 制品发布仍是独立手动工作流。源码发布不需要新增注册表权限、Packages 或生产凭据。若构建输入改变，需要重新验证受影响镜像；说明变更时不要把旧镜像的源码标签改成新提交来冒充重建。
