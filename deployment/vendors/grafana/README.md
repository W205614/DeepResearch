# Grafana 派生构建与待审查边界

`Dockerfile.grafana` 固定 Grafana 13.2.4 的真实 tag/commit、Go 1.27.3、依赖锁、官方运行时 digest、13 个完整官方插件 ZIP 的 SHA256 和 Alpine 安全包版本。源码与插件许可证保留在上游源码、运行时和完整插件包中。此目录保存补丁、依赖锁和来源记录。

保留全部 13 个插件。8 个插件的后端因 Go 或网络依赖漏洞需要重编；其真实模块版本、Git revision、`+dirty` 和依赖识别信息保留。其余 5 个继续使用官方二进制。核心前端 11,864 个文件与官方镜像逐文件相同，13 个插件的完整 690 文件集合也逐项核对；只有明确列出的 8 个 ELF 可不同。

修改后的 ELF **不再受原官方签名覆盖**。Grafana 先验证原 MANIFEST 的 PGP 签名，再检查所有原文件。派生验证器只在编译进当前 Grafana 主程序的精确插件 ID、后端相对路径、完整 MANIFEST 哈希、原后端哈希和新 ELF 哈希均匹配时接受替代后端。通过的派生插件显示 `private / DeepResearch maintainer rebuild`，不会声称为 Grafana Labs 签名。维护者承担这部分构建信任；没有运行时变量、配置、未知路径或未签名插件的扩大入口。

实际隔离测试确认：篡改已审核 ELF、前端 module.js、添加未知文件和增加未签名插件全部拒绝；未修改的官方插件仍正常。上游设置接口对已经记录的签名错误返回 500，测试同时要求明确的 `modified/unsigned` 拒绝日志。详见 `docs/evidence/optimization-20261026/grafana-tamper-isolated.json`。

**安全门禁仍未通过。** musl/musl-utils 已从1.2.6-r2升级至官方1.2.6-r3。新候选的完整 Trivy 原始报告为2 CRITICAL、11 HIGH、20 MEDIUM、2 LOW、5 UNKNOWN；完整 Grype 为2 HIGH、3 MEDIUM、5 UNKNOWN，所有级别都保留。3个 Grype MEDIUM 对应 BusyBox同一漏洞的3个包匹配，官方稳定包已经是最新r31且没有声明修复版本，仍作为中等风险保留。

独立 native binary cataloger 自动识别 Grafana 13.2.4/CPE；Go 的真实主模块伪版本导致旧应用 CVE 归因争议。Tempo 的精确 Git 依赖已包含两项修复，且真实 ELF 的 pclntab 只显示 Tempo protobuf 包，不含有关 frontend/S3 服务包。pclntab 不能单独排除所有内联指令。5个 UNKNOWN 是 GO-2026-5932：五个精确 ELF 的真实构建 flags、完整 go list-deps、实际模块版本和 pclntab 均已核对，告警列明的7个旧 openpgp包全部未进入编译依赖；主程序使用维护的 ProtonMail v1.4.1。其他 x/crypto包继续保留识别和扫描，不做模块整体豁免。

旧646bfcd6产物与13项提案已被替代，旧授权不得继承到新9a68d3a7产物。完整新提案是 `grafana-vulnerability-review-request-v2.json`，包括13项既有精确归因与新增5个逐 ELF、逐包的 GoVulnDB 归因；所有 Medium/Low和Unknown事实在 `grafana-all-severity-classification.json`。新subject没有获得授权，没有启用 VEX 或 CI 抑制。

上游 Makefile 使用 `-buildvcs=false`；当前派生构建仍保留真实 VCS。不能关闭 VCS、替换版本号或编辑 SBOM 来获得假零值。每次重建都产生独立产物，不能继承针对旧 digest 的判定或人类授权。

本机候选使用校验通过的官方 Git/模块/ZIP 运输缓存构建，随后只追加 Alpine 安全包更新和来源文件；完整网络源配方为 `Dockerfile.grafana`。不要把这次本机候选称为该网络配方的新鲜 CI 构建。Compose 禁用插件自动安装/更新，并使用已验证内置公钥；后续公钥、插件或依赖更新须重新构建、扫描和验证。

11.5.2 数据副本升级到 13.2.4、原 dashboard/datasource、Viewer/Admin/匿名权限和真实 Prometheus 查询已经隔离验证。回滚须恢复未迁移的原备份，不能将 13.x 已迁移的数据库直接交给旧版本。日常部署尚未执行。
