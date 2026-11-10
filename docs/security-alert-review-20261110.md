# 2026-11-10 Code scanning 修复与逐项核验

基线提交 `0005df53b742cdc2b79ef9196208cf0d00f8a0b8` 在默认分支有 70 条 open 告警：Trivy 65，CodeQL 5。它们包含重复依赖清单、原始历史材料与当前应用代码，不等同于 70 个独立可利用漏洞。

[70 条逐项登记](../deployment/security/code-scanning-alert-review-20261110.json)记录每条告警的编号、规则、原位置、源码摘要、具体依据和拟处置。它是审查依据，实际 fixed/dismissed 状态及最终 CI 以对应提交的 GitHub 结果为准。

| 范围 | 数量 | 处理依据 |
| --- | ---: | --- |
| CodeQL TLS 测试服务 | 1 | 显式限制最低 TLS 1.2；不授予例外，等待新扫描确认自动 fixed |
| CodeQL 两处 SSRF、CSRF、固定 SQLite 证据网页 | 4 | 真实双 origin HTTP／签名 JWT 回归及部署边界证明；精确 false positive |
| Trivy 当前具体构建 | 60 | 精确依赖版本、原生模块身份及完整 package closure；不宣称旧源码快照普遍安全 |
| Trivy 独立模块 | 4 | 对原先未验证的模块单独取得完整闭包，限定 Linux/amd64、CGO 禁用、无额外 build tags；明确保留补齐同版本校验和的边界 |
| Trivy 上游 telemetry 示例 | 1 | 提供真实编译验证的维护补丁，升级实际 MVS 漏洞节点并修正 SDK 身份；原快照保留，拟标记 mitigated |

## CI 门槛缺陷及修复

旧脚本只读取 SARIF `tool.driver.rules`。本次实际 CodeQL 产物的规则位于 `tool.extensions[].rules`，五条结果因此全部漏计，CI 日志错误报告 0。

修复按 SARIF 2.1.0 的 rule/component index 定位实际规则，同时保留原 `security-severity >= 7` 门槛。缺少规则、必需元数据、失败的扫描或不一致的索引必须失败，不能当作没有发现。原三份 SARIF 实际回放得到 5 条阻断项；不提供已审查记录时全部阻断。

[四项 CodeQL 精确记录](../deployment/security/codeql-reviewed-findings-20261110.json)仅匹配具体规则、文件、完整行列与消息，核对源码和证明文件 SHA-256。源码、测试或部署依据改变就需要重新审查；同规则其他位置仍阻断。原 TLS 告警没有例外，原始回放仍保持未解决。原 SARIF 未过滤或改写，正常扫描与上传持续执行，没有目录／规则类别忽略。

详细理由和本地真实回归见 [CodeQL 核验](../deployment/security/codeql-finding-review-20261110.md)。

## 依赖告警的适用性

初轮 [65 条 Trivy 审查](../deployment/security/code-scanning-review-20261110-evidence/review.json)保留当时五项 pending；它们的后续依据分别在 [四个独立模块](../deployment/security/code-scanning-review-20261110-evidence/standalone/README.md)与 [维护示例](../deployment/security/code-scanning-review-20261110-evidence/telemetry-example/README.md)，最终拟处置以 70 条登记为准。

没有以 UNKNOWN、位于测试目录或 CI 绿色为关闭理由。OpenPGP 依据官方列出的七个具体 import path，MongoDB 依据 GSSAPI 路径，Grafana 依据实际选中的固定模块／包；同一模块中未使用的漏洞包不等于整个旧模块都已修复。

27 套留存生产图重新解析并核对原生模块、源码锁及源文件连续性；额外取得当前 bootstrap 图、四个独立模块图和维护示例的真实图。公开图是保留全部包、Imports、Deps、ImportMap、模块版本／Sum／replacement 的显式派生投影，移除了主机目录，不冒充原始 raw。原始图、所有失败、补 sum 差异和旧扫描继续本地保留。官方最新公告原文及源身份随 [公开证据目录](../deployment/security/code-scanning-review-20261110-evidence/README.md)提供。

## 验收与交付边界

本地相关 Python 回归 103 项通过，补丁应用回归另 2 项通过；Java 安全回归 8 项通过，其中同一 Origin 测试 class 在隔离 Linux Temurin 21.0.13 中另执行 4 项通过。三个维护示例各自编译和 test-compile 的六个命令通过，没有测试用例或业务运行的声明。提交后仍须检查 CI、Security、实际 CodeQL 计数与逐条告警状态，排队、跳过、失败均不算通过。

本次不重发 Release 或镜像，不改日常生产配方／锁、认证运行代码、配置、数据及启停状态。现有应用镜像保留原版本；新的源码提交包含门槛、测试、逐项证据和限定示例补丁，不把它们声称为新的容器制品。

以后变更相关源码、依赖、构建 profile，或遇到新／变化的扫描结果，应重新核验并在必要时重新打开相关告警。历史 dismissed/fixed 记录与原始报告保留，不以页面计数代替安全结论。
