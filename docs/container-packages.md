# 应用容器 Packages

应用制品分成三个 Linux/amd64 包。Agent 包还用于 Worker、资料 Worker、迁移、数据库初始化、容量指标和告警中继；角色命令由原 Compose 服务配置决定。

当前正式版本仍为 `v0.1.3`，下表和安装命令继续指向已发布制品。2026-11-12 的 main 候选没有创建 `v0.1.4` Tag／Release、推送候选公开镜像或更新本机日常环境。[候选验证目录](evidence/release-v014-20261112/README.md)不代表正式版本：第一轮 `607626a2…` 真实流程完成（累计 32 次请求，上界预留 3.547734 USD），最终封装 Agent／Go 修复 MinIO 的同题组合 `8e83be7f…` 仍为 `insufficient`（累计 40 次、4.534515 USD）。后续仅离线补齐资料归因和引用位置解析，未新增真实调用，不能宣布最终验收通过或发布。

main 的 Grafana 继续由 `scripts/build_go_stdlib_security_20261112.py --artifact grafana` 使用 Go 1.27.4 本地源码重建；MinIO 改由 `scripts/build_minio_os_20261113.py` 同时修补原生依赖、curl 与 Thrift，入口见[运行依赖](source-release.md#运行依赖如何获取)。原 16 条 stdlib 记录随实际修复消失。MinIO 原 34 条 LOW/MEDIUM 的 17 条签名更新、4 条源码修复、13 条精确不适用依据，以及完整扫描仍保留的记录见[新技术范围](minio-native-security-20261113.md)；Grafana Trivy 40／Grype 10 保持。它们不是零漏洞声明或新的第三方二进制分发批准。下面 `v0.1.3` 的身份、历史验证和旧依赖获取方式不因此改写。

## 正式 v0.1.3

正式 [v0.1.3](https://github.com/W205614/DeepResearch/releases/tag/v0.1.3) 的源码与分发提交为 `78a19df9dfa6f072af6ebe40ff99cf164d0c4ff8`，[CI](https://github.com/W205614/DeepResearch/actions/runs/42968387539)全部 4 项、[Security](https://github.com/W205614/DeepResearch/actions/runs/42968387608)全部 22 项成功。[Packages 工作流](https://github.com/W205614/DeepResearch/actions/runs/42977626649)实际发布成功，三个原有 GHCR 目标保持公开，版本名和不可变 digest 均已实际匿名获取、拉取及运行核验。

| 公开包（版本均为 `v0.1.3`） | Registry manifest digest | Portable config digest |
| --- | --- | --- |
| [Agent](https://github.com/users/W205614/packages/container/package/deepresearch-agent) | `sha256:dd0ff5e0be21cf1ce7aed5f064752994d00dd2b11cb458e700372854e84d7c54` | `sha256:fbde5203f64cf35014e27a93717462506384e7b9afdb89b69ede5d2706d326bf` |
| [Backend](https://github.com/users/W205614/packages/container/package/deepresearch-backend) | `sha256:c63727c9a3ecbce4b5d4964e6a483a4535798bfa190e0b0b72b932c451459149` | `sha256:bef9e516a8daeaba7515ea24d4e6468d71ad3557694cbc91fe46349ecd1d5e89` |
| [Web](https://github.com/users/W205614/packages/container/package/deepresearch-web) | `sha256:013ba5daedc8d2d082f0b99ac88937781c8222c9321f28fdb8cd04ad253009e3` | `sha256:9ae657f7ba92e582275d1d472496d8e73009335dd645576ee752e774f5a84892` |

Manifest digest 用于 `docker pull ghcr.io/w205614/deepresearch-<service>@sha256:…`；portable config digest 是配置内容身份，不能代替 Registry 拉取地址。Agent／Backend 保留已经验收的 `v0.1.2` 完整 config、实际层、原内置 OCI 标签和 NOTICE（组件打包来源 `488550e`），没有伪造为重新编译。Web 真实修复官方 libxslt `1.1.43-r4`，最终扁平化移除旧层并更新法律与源码材料；整体版本和分发来源为上述 `78a19df`。

实际最终归档、云端发布加载制品和匿名拉取制品均执行安全复核：Agent 原始 88 条（3 HIGH、57 MEDIUM、23 LOW、5 UNKNOWN）逐项技术处置，适用未解决 0；Backend／Web 全部级别原始发现 0，三个制品 secret 均为 0。Agent 默认 raw-zero 失败及全部原始报告保留，精确门槛还核对完整 ELF／Python 闭包、官方 glibc 文件、SQLite、locale、运行配置与新增 OpenSSL 当前路径，具体边界见[安全复核](application-security-status-20261110.md)。

最终组合在隔离核心环境完成 4 个浏览器用例（0 跳过）、资料研究和引用检查，模型为固定夹具。Windows 宿主专用 TCP relay 只用于该隔离验收；普通日常端口由单独的日常启动／接口／运行指纹验收证明。原日常环境九个应用角色、Grafana 与两个 PostgreSQL 角色实际升级，其余十三个角色镜像身份保持；十四张业务表逐行摘要、十六卷、九网络、挂载及原配置保护项保持，验收后全部二十五容器恢复停止。

`v0.1.3` 交付阶段另复用 2026-11-10 在同一未变化 Agent 制品上的两次官方 API 实测：合成文本 JSON、3072 维 Embedding，均成功、0 重试，原总预算上限 1 USD；该交付阶段追加调用 0，未发送真实业务资料或重建历史向量。这些探针不等于全面模型质量评估。

可选 Grafana 是本地源码依赖 `deepresearch-grafana-source:20261110`，不是本轮公开 Packages。实际 Engine ID 为 `sha256:476eb9c63518bf1bd5776e66d15cdbe4198aea97201cc46902ceed16ec893f7c`；包含权限、软链等完整 15694 项文件清单 SHA 为 `d0707a9f15c66427df74f6c39daa3836dc47d294cc9fdf971e976b7a7c360010`，与干净 CI 一致。源码构建方法见[运行依赖](source-release.md#运行依赖如何获取)，保留旧固定输入制品，不另行公开新 Grafana 镜像。

## 历史 v0.1.1 公开制品

| 应用 | 固定版本示例 |
| --- | --- |
| Python Agent | `ghcr.io/w205614/deepresearch-agent:v0.1.1` |
| Java 业务后端 | `ghcr.io/w205614/deepresearch-backend:v0.1.1` |
| Web | `ghcr.io/w205614/deepresearch-web:v0.1.1` |

应用包与第三方服务分开维护。PostgreSQL、Keycloak、Milvus、MinIO／Silo、观测组件等继续采用原固定来源；三个应用包不是离线、自包含的完整环境。首次安装仍需取得这些依赖及供应商凭据。

截至 2026-11-10，三个包按原计划公开发布，Linux/amd64 匿名拉取已经验证；版本标签不覆盖。精确内容身份如下：

| 组件 | Registry manifest digest |
| --- | --- |
| Agent | `sha256:732fad95a682c28eedf66977b4860dafe971516fa7881a2908e9dee551ae4529` |
| Backend | `sha256:c12ef4e67f03a48a80de8de6f4bd9a4216fa60736cd84447fffb9889ca7b1188` |
| Web | `sha256:9819c4a5e0704bfbf697b8ddf7bee8516da545979a1cec31719f8529846329bd` |

打包提交为 `0005df53b742cdc2b79ef9196208cf0d00f8a0b8`，保留 `b8d1500` 的应用运行字节；源码 `v0.1.1` 为 `b2da9da7`。分发时补充法律材料并移除继承层中已删除／覆盖的旧内容，不把当前 `main` 说成旧镜像来源。

上述旧 Agent digest 原始扫描为 3 HIGH、43 MEDIUM、15 LOW；旧高危门槛只证明其 3 条 HIGH 的固定库代码、加载摘要与定向回归。新源码和候选中的 glibc 修复没有写回这个不可变旧 digest。Backend／Web 旧制品本次原始扫描为 0。镜像扫描与[70 条源码告警](security-alert-review-20261110.md)属于不同范围；本轮候选的完整结论见上节及[安全复核](application-security-status-20261110.md)。

## 获取与启动

以下为新安装的 `v0.1.3` 核心栈路线。覆盖文件兼容旧默认值，必须显式设置 `DEEPRESEARCH_PACKAGE_VERSION=v0.1.3`；否则会选择旧 `v0.1.1`。已有日常部署继续沿用原项目、配置、端口／网络覆盖和不可变 digest overlay，不直接套用新安装命令。

使用同一正式源码标签取得 Compose 覆盖和工具，需要 Git、Python 3.11+、Docker Linux/amd64 和 Compose v2。初始化后按[配置说明](source-release.md)填写私密凭据。固定 MinIO 镜像及第三方构建型服务仍须准备；下面 `build` 只选择第三方依赖，应用使用实际公开 Packages：

```powershell
git clone --config core.autocrlf=false --branch v0.1.3 https://github.com/W205614/DeepResearch.git
cd DeepResearch
python scripts/local.py init
# 填写本机 .env 的模型／嵌入凭据及可选重排配置后继续。
python scripts/local.py check
python scripts/fetch_approved_image.py
$env:DEEPRESEARCH_PACKAGE_VERSION = 'v0.1.3'
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml -f deployment/compose/compose.packages.yaml config --quiet
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml -f deployment/compose/compose.packages.yaml build postgres keycloak-postgres etcd milvus keycloak storage-init
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml -f deployment/compose/compose.packages.yaml pull agent backend web
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml -f deployment/compose/compose.packages.yaml up -d --no-build --wait --wait-timeout 600
```

第三方构建型服务首次安装仍按原运行说明准备，不能用上述应用包替代。已有部署保留原项目名、`.env`、端口／网络覆盖文件、账号及业务卷；在原配置后追加应用包覆盖，核对已有迁移与恢复条件，不用新安装参数覆盖日常环境。日常暂停、状态和日志均使用同一套参数：

```powershell
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml -f deployment/compose/compose.packages.yaml ps
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml -f deployment/compose/compose.packages.yaml logs --tail 100 agent backend web
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml -f deployment/compose/compose.packages.yaml stop --timeout 60
```

默认工作台 `http://localhost:8080`，Keycloak 管理台 `http://localhost:8180/admin`；首次操作见[首页研究流程](../README.md#启动后的第一条研究)。本机已有 25 容器的端口／网络覆盖和 digest overlay 属于私密部署配置，新安装不要复制或替换它。

这些 PowerShell 命令在同一会话执行；其他 shell 使用其环境变量语法。后续 `ps`／`logs`／`stop` 也保持相同变量和 Compose 参数。首次安装并非一次 `pull` 即取得完整第三方栈；有 `build` 的依赖仍会按固定源码构建。可选完整观测栈还须使用[受控 Grafana 源码入口](source-release.md#运行依赖如何获取)，不把旧公开输入当作新构建完成。

版本标签首次发布后不覆盖；正式部署可另加本机覆盖文件，将九个应用角色固定到上表三个 `ghcr.io/...@sha256:...`。不能仅凭版本名或 `latest` 判断字节身份。本轮三个既有公开包已用空 Docker 配置匿名拉取，不需要为获取它们增加个人令牌权限。

## 制品来源与许可材料

镜像内 `/usr/share/deepresearch-distribution/` 提供原创 MIT LICENSE、第三方声明、实际依赖／许可清单、保留的版权与许可文本，以及对应源码的准确获取说明。整个镜像不统一改为 MIT；第三方组件保留自身许可。

当前 [v0.1.3 Release](https://github.com/W205614/DeepResearch/releases/tag/v0.1.3) 的原 24 个附件继续保留：发布清单、验收摘要、17 个应用镜像分片和 5 个新对应源码分片；2026-11-12 另追加该阶段 CI 补充收据，合计 25 个附件。原发布清单 SHA-256 仍为 `a783597268a3d48f2154554e8517b8a2e9341d655822bdecc14fa117ec9e2ce8`。

对应源码采用 reference-v3，**不是自包含包**。先按该清单长度／摘要合并五个 `sources.tar.gz.partNNN`，得到 `distribution-source-reference.tar.gz`（82,413,180 字节，SHA-256 `4ace5acbf322d7f152d3f22aebc8f2cb1ff6423cabfd711bed2cf8d23f92ed5b`），解包阅读索引；其中包括工程归档、准确 libxslt r4 完整源码补充和重建配方。还必须取得固定 [v0.1.2 Release](https://github.com/W205614/DeepResearch/releases/tag/v0.1.2) 的 74 个源码分片并按其清单合并：旧清单 SHA-256 `890685dde617ffe3b8afbade8aa3cab61eb77e4c731b98e467cc11fd409ce9fd`，完整源码归档 1,227,806,395 字节，SHA-256 `5f87eea184278c15d621971cec53774d4177a1ca60a128d148d04888f2bcd88a`。合并按分片编号排序，以二进制方式拼接，先验证每个分片，再验证整包与索引成员，不能用文本拼接。新五分片及旧七十四分片的实际匿名完整获取和成员验证均已完成，曾发生的下载断流和范围恢复记录保留。

`v0.1.2` 源码 Tag 仍为 `488550ecb61e7f369dd4967c7fa35e4c64369e75`，正常 Release 的 93 个附件继续保留，承担上述对应源码职责。其旧 Packages 作业因新漏洞库发现被门槛阻断，失败发生在 Registry 推送前；不将它写作已发布的 `v0.1.2` Packages。`v0.1.3` 是真实 libxslt／工程修复版本，不为验证轮次重复创建版本。

历史 [v0.1.1 版本页](https://github.com/W205614/DeepResearch/releases/tag/v0.1.1) 自 2026-11-12 起集中提供源码说明及原应用制品 Release 的完整 88 个附件。原 Release ID `439279902`、全部 asset ID 和字节保持，只有版本页及浏览器下载路径迁移；源码 Tag 保持 `b2da9da7`，应用分发提交仍为 `0005df53`。它提供下列历史材料，不以当前源码身份替换：

- `publication-manifest.json`：最终源码、三个实际 image ID、分片长度／SHA-256、归档 SHA-256 和本地验收状态。
- 应用镜像归档分片：由经过验收的同一候选生成，发布工作流只校验、加载和推送，不重新构建。
- 对应源码与许可材料归档分片：实际 Debian／Alpine 版本的源码、补丁和构建配方，CPython／Temurin、相关 Python／Java 组件源码及原版权材料；具体覆盖与摘要见材料清单。
- 发布收据：三个包的不可变拉取地址及与候选 image ID 的对应关系。

镜像从无业务挂载、无运行过程的临时容器导出最终文件系统，排除 Docker 自动生成的主机名、hosts、resolv.conf 和运行标记，然后加上分发材料；保留原应用文件、权限、入口、环境、健康检查和信号设置。最终文件系统与新增许可文件分别核验，重新执行制品安全与运行验证。历史基础镜像中已被删除／覆盖的内容不会被作为隐含旧层再次分发。

源码版 `v0.1.1` 保留原标签。镜像的原运行源码 revision 和本次打包配方 revision 分开记录，不能把补充许可材料写作原应用重新编译。历史真实 API 证据的复用必须核对实际运行字节和配置；发布／拉取成功不代表模型质量、目标云环境、容量或异机恢复通过。

## 维护者发布

先确定源码提交、实际候选与许可材料，完成本地安全和运行验收，生成归档分片与清单。先确认版本、现有 Release 和镜像来源，核对所有材料后发布；同一待发版本复用草稿，不为重测或传输轮次新增版本。旧 `artifact-applications-v0.1.1-r1` 的附件已并入 `v0.1.1`，原清单的 `artifact_release` 字段保留历史值，仅供追溯；当前历史材料从版本页下载，不用它重新触发发布器或覆盖镜像。迁移和保留依据见[辅助发布记录](release-asset-retention-20261112.md)。保留失败及未知结果，不上传私密 `.env`、业务数据、恢复包、本机 AGENTS.md 或本地证据目录。

等待该提交 CI／Security 成功后，显式运行 [Publish verified application packages](../.github/workflows/publish-application-packages.yml)，传入对应单一 Release 标签与已验收清单的 SHA-256。发布器在推送前对实际加载归档执行完整 Trivy 漏洞和秘密扫描，默认要求全部级别 raw zero；Agent 仅可显式采用已绑定字节、运行配置、515 ELF／5972 Python 文件／290 glibc 项及逐条 tuple 的固定技术门槛。新发现、证据或配置变化失败，不能凭历史绿色 CI 或 `security` 布尔值放行。原始失败、扫描与裁决保留，秘密值不放进公开附件。

工作流使用仓库自己的 `GITHUB_TOKEN` 和 `packages: write`，不要求增加本机个人令牌权限；推送前检查命名空间及既有版本，拒绝覆盖不同字节的版本。

发布后核对包可见性，按预期身份实际拉取 digest，验证 config ID 与候选一致，再更新约定日常环境、验收并恢复原启停状态。收尾后才清理已经结束使用的普通临时文件；许可和对应源码材料继续随制品保留。
