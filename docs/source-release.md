# 源码发布与本地运行

应用 `v*` 标签发布源码和版本说明。当前正式 [v0.1.4](https://github.com/W205614/DeepResearch/releases/tag/v0.1.4)固定工程／分发提交为 `0b613f1386bcd7067cb54e236db6e29510c44b16`。三个应用 OCI Packages 已发布并完成匿名元数据身份核对，发布阶段未完整拉取或启动验收；该版 CI 使用明确豁免，原失败／取消记录保留，同提交 Security 22 项成功。见[精确身份与启动](container-packages.md#正式-v014)。源码 Tag、组件内置标签和文档 main 分别记录，不能把后续文档／CI 修订当成镜像来源。

正常历史 `v0.1.2` 源码为 `488550ecb61e7f369dd4967c7fa35e4c64369e75`，其 93 个公开附件（含 74 个对应源码分片）继续为新引用式源码包提供完整旧材料；该版 Packages 作业在推送前被新漏洞库阻断。最新 [对应源码获取方式](container-packages.md#制品来源与许可材料)要求同时获取新补充包和固定旧材料，不是只下载小包即可自包含重建。

历史源码 `v0.1.1` 保持 `b2da9da7`；2026-11-12 已把原 `artifact-applications-v0.1.1-r1` 的完整 88 个附件并入 [v0.1.1 版本页](https://github.com/W205614/DeepResearch/releases/tag/v0.1.1)，保留原 Release ID、asset ID、摘要和字节。旧应用分发来源仍为 `0005df53`，不冒充源码 Tag。独立第三方 `artifact-*` 的固定输入和来源绑定见[保留依据](release-asset-retention-20261112.md)。不移动产品标签，不因文档或验证轮次重复发布。

以下两段是正式发布前的历史记录。2026-11-12 当时的 main 候选仍在验证，没有创建 `v0.1.4` Tag／Release、推送候选公开镜像或更新日常环境。第一轮 `607626a2…` 同题真实研究完成，累计 32 次请求的上界预留为 3.547734 USD；最终封装 Agent 与修复后 MinIO 的组合 `8e83be7f…` 返回 `insufficient`，累计 40 次、4.534515 USD。剩余额度不足完整复验，之后仅离线补齐较长资料归因和显式引用位置的解析，未再调用真实模型。详见[候选材料](evidence/release-v014-20261112/README.md)；目录名不代表正式 Release，安全修复通过也不代表最终流程通过。

2026-11-13 在累计 10 USD 新上限内继续真实复验：原常规问法通过，复杂本地案例仅部分回答，联网案例因搜索外层等待超时失败。累计完整预留 9.518317 USD，未知请求保留，后续付费调用已停止；[本轮原始结果和截图](evidence/followup-20261113/README.md)不冒充最终安全修补组合已验收。当时正式版本仍为 `v0.1.3`；后续 `v0.1.4` 发布状态以上方及版本页为准。

## 克隆后启动

需要 Git、Python 3.11+ 和 Linux/amd64 Docker Engine（含 Compose v2）。Windows 使用 Docker Desktop 的 Linux 容器。Java、Node.js 与应用依赖由 Docker 构建；首次构建仍需下载上游镜像、源码和依赖，不能视为离线安装。

```powershell
git clone --config core.autocrlf=false --branch v0.1.4 https://github.com/W205614/DeepResearch.git
cd DeepResearch
python scripts/local.py init
```

初始化只在 `.env` 不存在时创建配置，生成独立的数据库、存储和管理端密码，不输出其值。已有 `.env` 原样保留；生成器的原配置副本 `.env.before-service-roles` 也只留在本机。填写 `LLM_API_KEY`、`EMBEDDING_API_KEY`；使用重排时填写 `RERANKER_API_KEY`，否则明确设置 `RERANKER_ENABLED=false`。

联网搜索有两层等待配置：`WEB_SEARCH_TIMEOUT_SECONDS` 限制一次完整搜索，`REQUEST_TIMEOUT_SECONDS` 限制其中一次供应商 HTTP 请求。当前 main 的新安装模板和 Compose 缺省值均为 120 秒；已有 `.env` 不会自动重写，显式配置的较短等待仍会生效，需维护者按实际需要同时调整两者。较短的外层等待会先取消仍在执行的请求；2026-11-13 的隔离测试保留了外层 20 秒、请求级 120 秒的超时失败，不能只提高请求级等待就认为搜索可等待 120 秒。取消后若未收到响应，供应商执行和费用仍未知，保留预留并核实后再决定是否发起新请求；增加等待时间不改变引用、事实或质量门槛，也不保证供应商会在时限内成功。

补齐服务凭据时支持 Windows UTF-8 BOM 配置；重复键按 Compose 的最后一次赋值判断，只补齐最终为空的值，不轮换最终已填写的密码。首次修改前的副本按原始字节保留，再次运行不会覆盖它。

```powershell
python scripts/local.py check
python scripts/local.py start
# 默认核心服务。暂停后保留容器、配置和数据。
python scripts/local.py stop
```

工作台默认 `http://localhost:8080`，Keycloak 管理台 `http://localhost:8180/admin`，管理员为 `admin`，密码从私密 `.env` 的 `KEYCLOAK_ADMIN_PASSWORD` 读取。工作台使用本地 IdP 注册的业务账号，不能拿 Keycloak 管理台账号直接当业务账号。

完整观测栈使用 `start --observability`，停止时同样加 `--observability`；上面检出的 `v0.1.4` 会先准备 MinIO，再按固定源码入口构建并核验 Grafana，具体入口见下文。`--project-name`、`--env-file` 和重复的 `--compose-file` 用于明确指定项目与覆盖配置，启动和停止须使用同一套参数。已有日常部署继续保留原项目名、端口覆盖和 Compose 文件，不要把首次安装命令直接用于替换现有环境。

`check` 只核对必需配置、Docker 平台、Compose 解析及所选 profile，不验证 API Key 是否有效，也不发出模型请求或构建镜像。`start` 默认构建；`--no-build` 仅用于已有匹配镜像的环境，不代表本次重新构建过。完整观测栈的 `--no-build` 还会核对 Grafana 实际镜像及缓存构建收据，缺失或不符会失败，不回退到旧镜像。

## 运行依赖如何获取

| 内容 | 来源与版本依据 |
| --- | --- |
| Java、Python、前端及其他带 `build` 的服务 | 从所检出标签中的 Dockerfile 和锁文件构建 |
| 核心 MinIO／Silo | `v0.1.4` 使用下述原生依赖修补入口与 `deepresearch-minio-source:20261113`；历史 `v0.1.3` 使用 `fetch_approved_image.py` 的固定输入 |
| 可选 Grafana | `v0.1.4` 使用下述统一入口与 `deepresearch-grafana-source:20261112`；历史 `v0.1.3` 使用 `scripts/build_grafana_source_20261110.py`／`deepresearch-grafana-source:20261110` |
| Redis、ClamAV 等上游镜像 | 使用 `compose.yaml` 中固定的版本与摘要 |

`v0.1.3` 阶段 PostgreSQL 配方实际更新官方 Kerberos、libxslt、zlib，保留 PostgreSQL 17.11／gosu；干净 CI 按源码配方通过。本机日常两个数据库角色使用签名 APK 在原准确制品上完成增量原生更新，完整运行配置、PostgreSQL／gosu 字节与十四张业务表逐行摘要保持；这是另行验收的本机路径，不称作完整无缓存冷构建或新公开 PostgreSQL Packages。Grafana 与其余第三方依赖也不因三个应用 Packages 已发布就自动取得完整基础栈二进制分发批准。

历史 `v0.1.3` 的 Grafana 路线使用已固定的前端与 13 个插件，重建主程序的官方 Thrift 0.25.0 依赖和 BusyBox HTTP 边界修复，并核对全部 14 个 ELF、编译图、源码输入、运行配置和完整文件清单。Compose 服务只引用该本地 tag；`docker compose up --build` 本身不会替它执行受控构建。检出 `v0.1.3` 时单独准备依赖可运行：

```powershell
python scripts/build_grafana_source_20261110.py --output-dir .cache/grafana-source-20261110
# 后续仅核对现有实际镜像与收据；不重新构建。
python scripts/build_grafana_source_20261110.py --no-build --output-dir .cache/grafana-source-20261110
```

这是本地源码构建入口，未新增公开第三方镜像，不继承旧 OCI 的安全批准；正常 profile 和原始全级别扫描的完整技术门槛独立核验。它仍依赖旧 `artifact-grafana-*` 中的精确输入与材料，不能删除旧 Release／附件或改变摘要。首次构建包含 Go／BusyBox 源码下载和编译，并非完全脱离旧镜像或离线安装。第三方许可和分发边界见 [THIRD_PARTY_NOTICES](../THIRD_PARTY_NOTICES.md)。

`v0.1.4` 沿用固定 Go 1.27.4 重建 MinIO／Silo 两个程序和 Grafana 主程序／十三插件；MinIO 另使用 2026-11-13 独立源码 profile 修补系统库、curl 和 Thrift。`scripts/local.py start` 会先准备 MinIO，带 `--observability` 时再准备 Grafana；`--no-build` 只核对现有实际镜像和收据，缺失或不符会失败。单独准备 `v0.1.4` 依赖使用以下入口，每次输出到新的证据目录：

```powershell
python scripts/build_minio_os_20261113.py --output-dir .cache/minio-source-20261113-build
python scripts/build_go_stdlib_security_20261112.py --artifact grafana --output-dir .cache/grafana-source-20261112-build
# 仅核对已有实际镜像；保留前次收据，使用新输出目录。
python scripts/build_minio_os_20261113.py --no-build --output-dir .cache/minio-source-20261113-verify
python scripts/build_go_stdlib_security_20261112.py --artifact grafana --no-build --output-dir .cache/grafana-source-20261112-verify
```

Go 升级使原 16 条 `CVE-2026-97054` 记录随真实修复消失，Grafana Trivy 54→40／Grype 10 保留。MinIO 后续对原 34 条 LOW/MEDIUM 逐项处理：17 条由签名包更新消除、4 条由实际源码修复、13 条有精确组件或架构不适用依据；另实际升级 curl／Thrift，消除补充扫描的 26 条记录。当前准备候选 Trivy 保留 24 条、Grype 保留 23 条，全部严重级别及完整库存受新精确门槛约束，不是 raw zero；静态 OpenSSL 的已知受影响条件也单独保留。[修补和验证边界](minio-native-security-20261113.md)区分源码、正常功能、恶意回归未执行及二进制分发范围。旧 MinIO 下载器已不作为 `v0.1.4` 的默认运行镜像入口，但历史版本、构建基底与来源校验仍使用其冻结材料，不能删除；上述本地构建也没有新增公开第三方镜像或分发批准。

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
