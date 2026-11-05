# 2026-11-05 安全维护记录

以下记录按维护阶段排列。首轮本地验证时，整体安全门禁仍有阻断。首轮检查时，远端 `main` 与本地已提交版本均为 `fbe9d05fe99490c09fa99b2b4ecd946d03217b14`。正式容器继续使用原镜像并保持停止，原业务数据、配置和停用备份策略保留。未调用模型、嵌入或重排供应商。

## 原始失败与本地修复

[2026-11-02 Security 运行](https://github.com/W205614/DeepResearch/actions/runs/41434950164)在同一提交上有 4 个作业失败；10 月 31 日的成功不能代替这次结果。

| 项目 | 原因 | 本地改动 |
| --- | --- | --- |
| Grafana、Java backend 原生扫描 | 固定的 10 月 26 日公告库超过 7 天限制 | 从 Anchore 官方 schema-v6 发布信息选择一次新鲜快照，保存原响应，固定该次归档的时间、schema 和 SHA256；保持归档校验、隔离导入、Grype 校验及扫描前后数据库完整性检查。发布信息不可用时直接失败，不回退旧库 |
| Keycloak | 遗留 `sqlite-libs` 命中 `CVE-2026-119373` 和 `CVE-2026-119586` | 通过 RPM 正常依赖检查移除旧 Red Hat JDK 遗留的 `nss`、`nss-sysinit`、`nss-softokn`、`sqlite-libs`，不用 `--nodeps` 绕过这些包的依赖。实际清单为 39 个 RPM、520 个 Java 包、1 个 JVM；原 JRE 文件清单保留 |
| agent | Debian 13 SQLite 3.46.1 命中 `CVE-2026-119373` | 固定安装 Debian 官方 `libsqlite3-0_3.54.1-2_amd64.deb`，校验 SHA256，保留真实 dpkg 包记录。该包来自 sid，仅依赖 `libc6 >= 2.38`，已验证在原 Debian 13/glibc 2.41 和 CPython 3.11.18 中加载；没有切换系统发行版或包仓库 |
| Java backend 新扫描结果 | 原镜像的 Expat 2.9.0 命中 4 个 HIGH | 将已有 APK 升级的版本下限提升为 `libexpat >= 2.10.0-r0`，保留原 JRE 和应用字节 |

SQLite 包的 SHA256 为 `7789bf0b2338b781de388d83d7c473f9386c9ef638af110b5c1b3d1debb36f00`，来自 [Debian 官方 sid 软件包索引](https://deb.debian.org/debian/dists/sid/main/binary-amd64/Packages.xz)。[Debian 安全记录](https://security-tracker.debian.org/tracker/CVE-2026-119373)将 sid 的 3.54.1-2 标记为已修复，同时将 trixie 的 3.46.1-7+deb13u2 标记为受影响。后续应优先换回经过验证的 Debian 13 正式修复包；当前不能据此宣称 Trivy 门禁通过。

## 已实际验证

- 112 项离线回归通过，覆盖公告发布信息、归档/导入/篡改拒绝、JVM 门禁、Grafana 收集入口、制品来源复用、当前选择约束和 CI Python 边界；本轮修改的 Python 文件通过 Ruff，`git diff --check` 通过。
- 实际取得 2026-11-05 构建的 Anchore 公告归档，校验 SHA256 后由固定 Grype 0.121.0 隔离导入，并核对实际数据库的 schema、构建时间、手工导入来源、路径和派生指纹。没有修改历史快照的日期或摘要。
- 使用该次已验证导入的数据库，重新执行真实 Syft/Grype 原生和完整清单扫描。Keycloak 候选的 HIGH/CRITICAL/UNKNOWN 为 0；升级 Expat 后的 backend 候选也为 0。原始报告保留，不代表所有等级告警均已清零。
- 两个候选中的真实 JRE 文件分别通过原清单校验：backend 255 项，Keycloak 257 项。
- 在无生产数据挂载的隔离 Keycloak 中验证启动、就绪检查、OIDC 发现、管理员令牌签发，以及默认 JCA、JSSE、AES/GCM 和随机数能力，结束后停止该测试容器。此结果不覆盖自定义 NSS/PKCS11/FIPS 部署。
- 在基于原 agent 运行时构造的候选中验证 SQLite 3.54.1、WAL/事务/重开数据库、FTS5 多段合并和查询、aiosqlite、LangGraph 检查点保存/恢复及 API 导入；UID 为 10001，供应商请求为 0。
- 固定 Trivy 0.75.0 和当天经过 OCI 归档摘要核验的真实公告库：Keycloak OS 清单不再包含 SQLite，HIGH/CRITICAL 为 0；agent 与 Grafana 完整扫描保留了全部等级、包清单和 secret 结果，secret 均为 0。

上述候选复用了现有已验证运行时，再执行本轮相同包操作，用于核实实际字节、依赖和行为。它们没有替换正式容器。Keycloak 完整标准 Dockerfile 构建曾因远端下载 `unexpected EOF` 中断，不能把候选验证称为完整标准构建成功。独立收集入口再次下载公告归档时也遇到截断；本地重扫复用的是本轮已成功校验并隔离导入的那一次数据库，原失败记录保留。

## 首轮阻断记录（续修结果见下）

1. **agent 的 Trivy HIGH 门禁仍失败。** 完整扫描仍列出 `CVE-2026-119373 / libsqlite3-0 3.54.1-2`，来源为 Debian 发行版公告数据，未提供 trixie 修复版本。与 Debian 对 sid 包的已修复判定存在差异，尚未通过发行版正式修复或独立处置闭环解决；没有加入 ignore 或改变退出语义。
2. **agent 还列出 `CVE-2026-76844 / zlib1g`。** 原始报告标题混有 webpack 信息，但 [CVE 官方记录](https://github.com/CVEProject/cvelistV5/blob/main/cves/2026/76xxx/CVE-2026-76844.json)及 [zlib 上游补丁](https://github.com/madler/zlib/commit/df84af25dc1942490e1d1c899a07619152a46148)描述了 zlib 写入失败后的缓冲区问题。不能因标题不一致就认定误报。本轮未完成该运行库的补丁、依赖和扫描验证，继续保留阻断。
3. **Grafana 当前精确制品与原逐项授权不再匹配新公告结果。** 完整原生报告出现 `GO-2026-6860` 等匹配变化，既有 `verify_full_scans` 实际拒绝：`Exact7 native blocking findings are missing, extra, duplicate or changed`。[Go 官方公告](https://pkg.go.dev/vuln/GO-2026-6860)指出 x/net/html 在 v0.62.0 前受影响。没有扩大旧授权、改写固定制品或新增风险豁免。要继续交付，须对新的实际制品或准确适用性事实完成独立复核；原授权不能自动沿用。
4. **远端检查没有重跑。** 本轮按要求不提交、不推送、不部署，不能宣称 GitHub Security 已恢复，也不能称正式容器已包含本轮修复。Keycloak 的本地完整 Trivy Java 扫描因 Java 公告库下载中断未完成；其 Java/JVM 清单另由上述真实 Syft/Grype 扫描覆盖。

本机原始报告与构建/功能验证记录位于 `.cache/security-fix-20261105/`，失败的独立 Grafana 收集记录位于 `.cache/eval/security-fix-20261105-grafana/`。这些本地材料不属于已发布制品或恢复集。

## 本次续修进度

### agent 已完成本地闭环

在标准 Dockerfile 中同时固定安装经过校验的 SQLite 3.54.1-2 和 zlib `1:1.3.dfsg+really1.3.2-4` 官方 Debian 包。后者包含 Debian 回移的 zlib 上游 `df84af25dc1942490e1d1c899a07619152a46148` 补丁，不以 1.3.2 的版本字符串代替补丁证明。

Trivy 的 Debian 检测器对没有发行版修复版本的公告直接报告受影响，不比较实际安装版本，见[固定扫描器的官方实现](https://github.com/aquasecurity/trivy/blob/v0.75.0/pkg/detector/ospkg/debian/debian.go)。因此增加 `check_agent_native_packages.py`：先保留包含所有等级、全部包和 secret 的原始扫描，再对精确 Debian 条目验证实际 dpkg 身份、官方修复 ELF 的 SHA256、Python 实际加载路径和加载字节。库文件与 SONAME 重定向到旧库时不能通过。所有其他 HIGH/CRITICAL 和任何 secret 继续阻断；UNKNOWN、LOW、MEDIUM 按原 agent 门槛保留，不修改原始报告、不使用 ignore、VEX 或忽略未修复选项。

该门禁绑定了两份官方包的精确字节和公告/补丁证据，文件为 `deployment/security/agent-native-packages.json` 与 `agent-native-evidence/`。对 SQLite 仅处理该修复版本下的 `CVE-2026-119373`；对 zlib 仅处理同一上游修复对应的 `CVE-2026-76844` / `CVE-2026-85091`。新增 ID、不同包、不同版本、不同字节、不同数据源或升为 CRITICAL 都不会继承此判定。

实际验证结果：

- 标准 Dockerfile 完整构建成功，镜像为 `sha256:4a3b5a0e7ab54fbcf6bc8fb9eb5daba738149bba4e5da139dba6649306c53bf0`；原正式容器未替换。
- 标准产物完整 Trivy 扫描运行成功，保留 2 HIGH、6 UNKNOWN、41 MEDIUM、11 LOW，secret 为 0。两条 HIGH 经实际修复字节核验后，必过门禁通过，未解决 HIGH/CRITICAL 为 0。这不是“原始扫描零告警”。
- 门禁在真实镜像中执行 SQLite 上游负 rowid、`detail=none` 前缀查询回归，结果为 56；对 zlib 执行 100 次写入失败/清错调用及压缩解压往返，均通过。
- 标准产物的 WAL、重开数据库、FTS5 合并、aiosqlite、LangGraph 检查点恢复与 API 导入通过，UID 为 10001，无供应商请求。
- Security 工作流已接入完整原始扫描和必过字节门禁；其他应用门槛不变。原 Grafana 加载、扫描、逐项判定和授权保持原样，已实际比较 `workflow_facts` 未变化后，仅更新当前 Security 选择器的复核指纹。

本机明细位于 `.cache/security-fix-20261105/continued/agent-completion.json`。最终门禁复验为 `agent-canonical-native-final-id/verdict.json`，检查容器按已检查的不可变镜像 ID 创建。

### 初次候选验证与待审状态

进一步查阅 Grafana 官方公告确认，当前 13.2.4 还命中三项需要实际升级的公告：[SSO 授权](https://grafana.com/security/security-advisories/cve-2026-103506/)、[告警联系人授权](https://grafana.com/security/security-advisories/cve-2026-100105/)、[Azure Monitor SSRF](https://grafana.com/security/security-advisories/cve-2026-88967/)，官方修复版本为 13.2.5。`GO-2026-6860` 的别名和五个插件目标已有历史 HTML 适用性材料，但其新扫描结果仍须重新核对，不能直接扩大旧授权。

已实际拉取并完整扫描官方 13.2.5 镜像。它仍带有旧插件、go-jose 和旧 Go 依赖，两种扫描都未通过，因此没有将它直接选入正式编排。

新增 `deployment/docker/Dockerfile.grafana-maintenance`，从 Git 对象核对官方 13.2.5 提交 `ce471d869a4810cd83c987dd3521c4b67689dbb6`，使用已有 Go 1.27.3 与依赖锁重建主程序，保留原精确制品的插件字节和原有签名验证机制，记录完整生产依赖图。已对真实 13.2.5 源文件和八组实际插件/签名清单验证现有补丁生成器可用。该 Dockerfile 目前是本地候选，未被正式 Compose 或 CI 选择，没有继承旧制品授权。

候选已完成标准 Dockerfile 构建，插件来源也已固定为原审核镜像的精确摘要。最终 OCI 索引为 `sha256:5a14435f088e735cbaba6e6375c5ea03e34424ea3934b55e098a2fb660db452a`，运行时 manifest 为 `sha256:0f354ce9c25e92e811c3f09382c0d317bc7193bee58b97e4b1170049f2b007aa`，config 为 `sha256:bde24c79bbb09619d33a912b8487e573e3ddbbdadf4f923afe3b4f0988a439fe`。本地 OCI 归档为 776,297,984 字节，SHA256 为 `408744f9d8d357a9b7dfd97ac802efbbdee662cb1177b80d8020bdc24c019e25`；由原有有界 OCI 校验器重新核对完整 blob、DiffID 和最终 14 个 ELF，验证通过。

实际验证结果：

- 主程序实际为 Go 1.27.3、go-jose 4.1.6、x/net 0.62.0，上游提交与构建记录一致。4199 个完整生产依赖包没有错误、缺失导入或重复包，521 个实际二进制依赖与完整图一致；旧 `golang.org/x/crypto/openpgp` 包不存在。13 个插件 ELF 与原精确制品逐字节一致。
- 两种完整扫描均运行成功，三项上述 Grafana 产品公告不再出现。Trivy 原始结果仍有 2 CRITICAL、11 HIGH、15 UNKNOWN、20 MEDIUM、2 LOW，secret 为 0；Grype 仍有 7 HIGH、5 UNKNOWN、8 MEDIUM。保留所有原始条目，不把扫描命令的运行成功视作安全门禁通过。
- 在 `network=none`、没有业务数据挂载或主机端口的独立容器中，数据库就绪、版本 13.2.5 和 13 个插件的有效签名均通过。修改后端 ELF、修改签名前端文件、增加未知文件、增加未签名插件这四种情况都被拒绝，HTTP 500 与精确签名拒绝日志相符；未改动的 Prometheus 插件仍有效。篡改测试与最终镜像的运行时 config、DiffID 和字节相同，仅重建的来源证明索引不同。所有测试容器均已停止。

可复核材料见 `deployment/security/grafana-maintenance-preparation.json` 与 `grafana-maintenance-evidence/`，包含精确制品身份、构建输入指纹、完整原始报告的无损压缩副本、逐项扫描行、依赖图收据、签名/篡改结果及旧授权拒绝记录。完整主程序依赖图同时保存在候选镜像内和本机 `.cache/`，其指纹已记录。这些是准备材料，尚无新的独立逐项复核或人工授权记录，不是可供正式加载器接受的授权文件。

**初次候选验证时，Grafana 仍待逐项复核授权。** 原 `check_grafana_candidate_exact28.py` 的 `load_request` 要求新的独立复核，`validate_authorization` 要求授权绑定精确的新请求和新制品。将旧授权实际应用于新候选会明确拒绝：`Authorization binds another request or subject`。当前门禁仍选择原授权制品，不能将旧 exact7 数量改大、自动继承 28 条旧判定或仅更新镜像摘要来使其通过。剩余步骤为新制品及实际扫描行的独立复核、精确授权，以及在另行允许发布后更新制品传输和 CI 选择；本轮未发布或修改正式选择。

### 初次候选验证结束时的运行状态

- 定向回归为 216 通过、2 跳过；跳过项是 Windows 缺少符号链接权限的既有测试，未计作通过。最后将 agent 检查容器改为按不可变镜像 ID 创建后，相关 22 项测试及真实镜像门禁再次通过。Ruff 与 `git diff --check` 通过。
- 正式 25 个容器的 ID、镜像、挂载和停止状态与操作前一致，独立安全测试容器均停止。业务数据与配置没有替换。
- `DeepResearch-Verified-Cold-Backup` 仍为 `Disabled`。未调用模型、嵌入或重排供应商。
- 本地与远端 `main` 的已提交版本仍为 `fbe9d05fe99490c09fa99b2b4ecd946d03217b14`；修复仅在本地工作区，未提交、未推送、未部署，远端 CI 未重跑。


### Grafana 13.2.5 逐项复核与精确授权

主程序和插件分别完成独立复核。审查读取实际 ELF、Go 生产依赖图及上游公告，并按扫描器、公告、目标、包、版本、严重级别与完整原始报告逐行对应。Trivy 的 28 行与 Grype 的 12 行合计产生 33 个不同的授权条目，其中 5 条 HTML 告警保留 Grype 的 HIGH 严重级别。复核记录见[主程序](../deployment/security/grafana-maintenance-evidence/core-independent-review.json)和[插件](../deployment/security/grafana-maintenance-evidence/plugins-independent-review.json)。

本次精确授权绑定请求摘要 `4e06f257a627bbbecaf3f2c3d867b8f0edbe5be7e32c317660b1296a1d8c7758`，完整归档、扫描结果和实际加载镜像通过本地门禁。正式加载命令和镜像标识见[部署与维护](deployment-security-operations.md)。

完整归档已按 12 个分块发布，每个分块的编号、长度和 SHA256 均经 GitHub 核验。正式选择绑定发布记录、逐项授权与运行时摘要。参见[本次制品](https://github.com/W205614/DeepResearch/releases/tag/artifact-grafana-maintenance-20261105)。

### 首次远端检查后的修复

Grafana 的精确制品门禁和 agent 的原生库门禁在提交 `c61d466` 的远端 Security 中通过。该次扫描还发现前端镜像的 Expat 2.9.0 和 Milvus 镜像的 APT 2.4.14 受影响。前端运行镜像要求 `libexpat >= 2.10.0-r0`，Milvus 构建和运行镜像使用 Ubuntu Jammy 修复包 `apt`、`libapt-pkg6.0` 2.4.15ubuntu0.1。两个本地修复候选的完整扫描均无 HIGH、CRITICAL 或 secret。Milvus 本地候选复用已核对的同源二进制并更新 APT 包，标准源码构建由 CI 执行。

同次企业栈构建收到的 etcd 时区包与固定 SHA256 不符。独立核验 Debian 签名索引和官方包字节后，原校验值正确。本地重新执行原配方的信任材料和运行时包装阶段均通过，保留签名与字节校验。
