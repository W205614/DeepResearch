# 源码发布与本地运行

应用 `v*` 标签发布源码和版本说明。本版 [v0.1.4](https://github.com/W205614/DeepResearch/releases/tag/v0.1.4) 交付常规资料问法的确定性校验修复；最终源码／分发 SHA、三个应用 OCI 的精确身份、发布和 CI／Security 收据见版本页的 `publication-manifest.json` 及最终验收附件。Agent 同步本版应用代码和法律封装，原生依赖字节保持；Backend／Web 精确复用已有 config 与层。源码 Tag、分发 cohort 版本、组件内置标签和文档 main 分别记录，不能把复用组件说成重新编译，见[制品边界](container-packages.md#正式-v014)。

历史 `v0.1.3` 固定工程／分发提交仍为 `78a19df9dfa6f072af6ebe40ff99cf164d0c4ff8`，其已公布收据和旧镜像身份保持。`v0.1.4` 的新引用式对应源码仍依赖这批补充材料及下述 `v0.1.2` 完整旧材料，不是新独立、自包含的依赖源码包。

正常历史 `v0.1.2` 源码为 `488550ecb61e7f369dd4967c7fa35e4c64369e75`，其 93 个公开附件（含 74 个对应源码分片）继续为新引用式源码包提供完整旧材料；该版 Packages 作业在推送前被新漏洞库阻断。最新 [对应源码获取方式](container-packages.md#制品来源与许可材料)要求同时获取新补充包和固定旧材料，不是只下载小包即可自包含重建。

历史源码 `v0.1.1` 保持 `b2da9da7`；2026-11-12 已把原 `artifact-applications-v0.1.1-r1` 的完整 88 个附件并入 [v0.1.1 版本页](https://github.com/W205614/DeepResearch/releases/tag/v0.1.1)，保留原 Release ID、asset ID、摘要和字节。旧应用分发来源仍为 `0005df53`，不冒充源码 Tag。独立第三方 `artifact-*` 的固定输入和来源绑定见[保留依据](release-asset-retention-20261112.md)。不移动产品标签，不因文档或验证轮次重复发布。

## 克隆后启动

需要 Git、Python 3.11+ 和 Linux/amd64 Docker Engine（含 Compose v2）。Windows 使用 Docker Desktop 的 Linux 容器。Java、Node.js 与应用依赖由 Docker 构建；首次构建仍需下载上游镜像、源码和依赖，不能视为离线安装。

```powershell
git clone --config core.autocrlf=false --branch v0.1.4 https://github.com/W205614/DeepResearch.git
cd DeepResearch
python scripts/local.py init
```

保留上述完整 Git 克隆。受控依赖构建还要核对固定历史审查快照（包括 `78a19df9`）；缺少该历史对象的浅克隆或单独源码 ZIP 不能直接替代。对应源码引用材料的长度、摘要和合并方式以版本清单为准。

初始化只在 `.env` 不存在时创建配置，生成独立的数据库、存储和管理端密码，不输出其值。已有 `.env` 原样保留；生成器的原配置副本 `.env.before-service-roles` 也只留在本机。填写 `LLM_API_KEY`、`EMBEDDING_API_KEY`；使用重排时填写 `RERANKER_API_KEY`，否则明确设置 `RERANKER_ENABLED=false`。

补齐服务凭据时支持 Windows UTF-8 BOM 配置；重复键按 Compose 的最后一次赋值判断，只补齐最终为空的值，不轮换最终已填写的密码。首次修改前的副本按原始字节保留，再次运行不会覆盖它。

```powershell
python scripts/local.py check
python scripts/local.py start
# 默认核心服务。暂停后保留容器、配置和数据。
python scripts/local.py stop
```

工作台默认 `http://localhost:8080`，Keycloak 管理台 `http://localhost:8180/admin`，管理员为 `admin`，密码从私密 `.env` 的 `KEYCLOAK_ADMIN_PASSWORD` 读取。工作台使用本地 IdP 注册的业务账号，不能拿 Keycloak 管理台账号直接当业务账号。

完整观测栈使用 `start --observability`，停止时同样加 `--observability`；本版会先按受控源码入口准备 MinIO／Silo，并在启用观测时准备 Grafana，详情见下表。`--project-name`、`--env-file` 和重复的 `--compose-file` 用于明确指定项目与覆盖配置，启动和停止须使用同一套参数。已有日常部署继续保留原项目名、端口覆盖和 Compose 文件，不要把首次安装命令直接用于替换现有环境。

`check` 只核对必需配置、Docker 平台、Compose 解析及所选 profile，不验证 API Key 是否有效，也不发出模型请求或构建镜像。`start` 默认构建；`--no-build` 仅用于已有匹配镜像的环境，不代表本次重新构建过。`--no-build` 仍会核对 MinIO 实际源码镜像及构建收据；启用观测时也核对 Grafana，缺失或不符会失败，不回退到旧镜像。

## 运行依赖如何获取

| 内容 | 来源与版本依据 |
| --- | --- |
| Java、Python、前端及其他带 `build` 的服务 | 从所检出标签中的 Dockerfile 和锁文件构建 |
| 核心 MinIO／Silo | `scripts/build_go_stdlib_security_20261112.py --artifact minio` 构建并核验本地 `deepresearch-minio-source:20261112`；原固定来源与材料仍按绑定保留 |
| 可选 Grafana | `--observability` 使用同一受控构建器的 `--artifact grafana`，核验本地 `deepresearch-grafana-source:20261112`；仍依赖固定历史输入与审查快照 |
| Redis、ClamAV 等上游镜像 | 使用 `compose.yaml` 中固定的版本与摘要 |

`v0.1.3` 阶段 PostgreSQL 配方实际更新官方 Kerberos、libxslt、zlib，保留 PostgreSQL 17.11／gosu；干净 CI 按源码配方通过。本机日常两个数据库角色使用签名 APK 在原准确制品上完成增量原生更新，完整运行配置、PostgreSQL／gosu 字节与十四张业务表逐行摘要保持；这是另行验收的本机路径，不称作完整无缓存冷构建或新公开 PostgreSQL Packages。Grafana 与其余第三方依赖也不因三个应用 Packages 已发布就自动取得完整基础栈二进制分发批准。

本版对 MinIO／Silo 的两个程序和 Grafana 主程序／十三插件分别采用 Go 1.27.4 重建，保留各自既有修复、其他运行内容和完整来源边界。构建收据核对源码输入、真实 ELF、config、层和完整文件清单；安全结论仍由对应扫描与精确技术门槛单独给出，构建完成不等于扫描通过。MinIO 的遗留 LOW／MEDIUM 未据此宣称全量审查完成。

源码启动器自动调用受控入口；直接使用 Compose 前可以单独准备：

```powershell
python scripts/build_go_stdlib_security_20261112.py --artifact minio
# 仅完整观测栈需要。
python scripts/build_go_stdlib_security_20261112.py --artifact grafana
# 后续仅核对已有实际镜像、固定来源和完整收据，不重新构建。
python scripts/build_go_stdlib_security_20261112.py --artifact minio --no-build
python scripts/build_go_stdlib_security_20261112.py --artifact grafana --no-build
```

每次调用使用新的本机证据目录；`--output-dir` 可显式指定尚不存在的 `.cache` 子目录。Compose 只引用上述本地 tag，`docker compose up --build` 不能替代受控入口。本地源码构建不批准新增公开第三方二进制分发，也不继承旧 OCI 的安全结论。旧 `artifact-*` 的准确输入、下载绑定和来源材料继续保留；首次构建需要网络下载和编译，不是离线安装。第三方许可和分发边界见 [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)。

历史源码 `v0.1.1` 的启动仍使用它原来的 Grafana loader，不具有上述新入口；需要复现时明确检出 `v0.1.1`，不要混用当前 main 的脚本。三个旧应用镜像及材料从[历史应用制品入口](container-packages.md#历史-v011-公开制品)获取。源码发布本身不代表所有第三方制品的二进制分发验收完成。

单文件镜像下载若在声明的长度之前正常断流，加载器仅向同一可信存储地址最多补传两次剩余字节，严格核对 HTTP Range 和最终完整 SHA-256；不会把截断文件当成成功，也不向存储站转发 GitHub Token。仍失败时保留 partial 文件并停止，排查网络后重新运行启动命令。

Temurin JRE、签名／SBOM 和 Prometheus／Alertmanager UI 从固定官方 Release asset ID 下载，保持原版本、SHA-256 和已有 PGP 校验。构建传输最多额外尝试两次，单次限时 180 秒；错误页面不会成为新的预期摘要。原生扫描器从锁定版本的官方 Release API 核对资产名称、长度和摘要后下载，扫描工具、数据库门槛及镜像选择不变。

## 维护者发布步骤

1. 检查本次差异、许可证和待发布源码内容，不携带 `.env`、业务数据、恢复包或本地证据。源码与第三方镜像分别按实际分发内容核查。
2. 提交并推送；等待该提交的 CI、Security 和适用的观测规则检查完成。CI 的源码归档检查不等同于镜像许可检查。
3. 从最终提交的干净副本执行 `init`、`check`、构建启动和核心回归，记录复用的 Docker 缓存、外部镜像和未运行项目。停止隔离测试栈；不要操作既有业务卷。
4. 对最终提交运行 `python scripts/verify_source_release.py --ref HEAD --output .cache/source-release`。它核对 Git 归档内的必要文件、私密路径和符号链接，并输出源码逐文件摘要。另行检查内容中的真实凭据，路径检查不能代替秘密扫描。
5. 确有需要交付的版本变更时，为最终提交准备语义化标签与 Release 草稿；核对目标、源码／镜像关系、变更、安装与限制后正式发布。不为同一版本的重测或文档更新新建标签，也不覆盖历史标签。源码归档使用 GitHub 自动生成入口；镜像、许可和对应源码按实际制品另行交付，`.cache/source-release` 只作本地核对材料。

现有常规 CI 只构建和验证，不因应用标签或 Release 自动推送镜像或部署生产。应用容器由[显式 Packages 工作流](../.github/workflows/publish-application-packages.yml)校验已验收制品后推送；Grafana 也保留独立手动工作流。Maven 用于源码编译与测试，不发布 Maven 应用包。纯文档或测试变更不要求重建镜像；构建输入改变时重新验证受影响制品，不能把旧镜像的来源标签改成新提交。
