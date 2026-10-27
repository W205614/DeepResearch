# 2026-10-26 架构、可靠性与安全优化证据

当前阶段：`DEPLOY_READY`。候选实现、五角色功能/故障验收和监控验收已取得对应证据，远程制品重组和新增 Windows 静默任务入口检查已完成。尚未提交最终 Git SHA、替换日常部署或取得最终 GitHub CI 结果。逐项状态以 [scope.json](scope.json) 为准，不能将组件通过理解为全部交付完成。

后续窗口状态：`DR DAILY_DEPLOYMENT_NO_CLEANUP` 已获批准。用户已确认手动停机并要求启动继续，由 root 独占执行日常 Docker 变更；启动、备份/恢复及日常验收结果仍须另有执行证据。该窗口不包含清理许可。

## 架构判断

Java 控制身份、权限、任务接纳、发布审核和最终业务状态；Python 执行研究图、检索、解析及受控恢复，PostgreSQL 保存业务事实。这个划分适合当前单机内部试用。无需为了“微服务化”再拆出更多独立部署单元。

原来的主要不足在于跨语言边界和故障状态：恢复资格可能不一致，首次扫描缺少完整恢复闭环，SQL 删除与检查点/对象存储清理不能原子提交，私有历史资料必须在撤权后再次过滤。修复采用共享版本契约、尝试号隔离、持久清理记录和逐次授权，不以更多模型节点替代这些控制。

文档解析迁入一次性受限进程；大语料关键词召回改为数据库派生词索引；范围确认、资料集合、费用和公开来源重核验拆成独立界面/服务。仍有较长的 `Runtime`、研究图与工作台文件；当前采用稳定契约下的渐进拆分，未宣称结构债务全部消失。

`RunCleanup` 进一步分离事务内删除准备和事务外可重试清理，核验目标采用类型契约，资料界面采用空间代次隔离异步响应。成员管理的真实浏览器验收另发现保存刷新会清空下一条输入，已加入提交保护、输入身份和空间响应校验。

## 可核对的实现与验收

| 项目 | 当前证据 | 尚待完成或证据边界 |
| --- | --- | --- |
| 0 基线与共同交付 | 冻结输入、分项账本、独立候选项目 | 日常冷备/恢复、部署窗口、定向清理、最终 SHA 与 CI |
| 1 恢复与异常分类 | [reliability.json](reliability.json)、[recovery-and-sources.json](recovery-and-sources.json) | 一次公开任务出现约266秒停顿后自动恢复，未复现；原因仍在核查 |
| 2 费用治理 | [governance.json](governance.json)、[usage-postgres.json](usage-postgres.json) | 运维探测与静态检查区分；已知实耗、未知预留分开；真实供应商验收另记 |
| 3 上传扫描恢复 | [reliability.json](reliability.json)、[dependency-faults.json](dependency-faults.json)、[browser-failures.json](browser-failures.json) | 真实 ClamAV 故障暴露的退避问题已修复并重验；MinIO 断服返回受控503，同内容重传复用记录 |
| 4 解析资源边界 | [parser.json](parser.json) | Windows 与 Linux 有针对性验证；不等同完整恶意代码沙箱 |
| 5 删除与保留 | [reliability.json](reliability.json)、[reliability-postgres.json](reliability-postgres.json) | 实际 PostgreSQL 并发、检查点与文档向量故障重试通过；来源不明的独立语义记忆物理保留，失去原任务后拒绝上下文召回 |
| 6 服务最小权限 | [privileges-isolated-v3.json](privileges-isolated-v3.json)、[storage-isolated.json](storage-isolated.json)、[新鲜库供应商扫描](vendor-fresh-scan.json)、[精确 Grafana 门禁](grafana-artifact-governance.json) | 本地普通镜像扫描与获批制品定向判定分别验证；最终 SHA 的冷 Security CI 尚未执行，原始中低危和 Unknown 不删除 |
| 7 检索容量边界 | [retrieval.json](retrieval.json) | 固定题与4201片段实验，不是通用召回率、容量或延迟承诺 |
| 8 研究范围确认 | [scope-intake.json](scope-intake.json)、[roles-and-scope.json](roles-and-scope.json) | 确定性保守触发规则，不保证识别所有模糊问题 |
| 9 资料与历史权限 | [acl.json](acl.json)、[roles-and-scope.json](roles-and-scope.json) | 当前权限重查覆盖检索、来源、记忆、报告与导出；异人角色由自动化浏览器操作 |
| 10 公开来源时效 | [source-revalidation.json](source-revalidation.json) | 来源正文变化不是事实错误证明；浏览器正文和模型为受控夹具 |
| 11 跨语言契约 | [reliability.json](reliability.json) | 共享冻结样例、严格版本/字段、尝试号和数据库写入边界 |
| 12 模块与运维 | [boundary-review.json](boundary-review.json)、[infra.json](infra.json) | 配置档、资源限制、告警、备份工具已实现；安装与日常部署证据待窗口阶段 |

[document-lifecycle-roles.json](document-lifecycle-roles.json) 验证实际文件上传、替换、影响分析、报告修订、独立审核账号发布、只读访问和撤权。五角色、终态恢复及公开来源修订使用真实 OIDC、Java/Python、PostgreSQL、Redis、MinIO、Milvus、ClamAV；模型、嵌入或网页正文的替换点明确标为夹具。没有全程拦截浏览器业务 HTTP。

[统一候选验收](unified-candidate.json) 已切换正式完整构建的Python3.11.18、签名Java21.0.13+9及更新后的数据/身份组件；[五个应用服务的源码指纹](candidate-source-fingerprints-v5.json) 均匹配。重新验证五角色、Java恢复、资料生命周期和真实扫描/对象存储故障路径，无新增供应商调用。升级前保留11个合成数据卷冷快照，另以只读SQL核对[163条选定旧行和11份旧正式快照](upgrade-old-row-preservation.json)，均保留且固定字段/正文摘要一致。这些结果不是完整存储恢复或日常部署证明。

失败尝试、修复前后结果和私密原始材料的摘要分别保留。测试脚本错误、已确认缺陷、尚未定位的停顿与供应商扫描失败不混写成“全部通过”。仅重测修改和直接影响路径，完整检查交最终 GitHub CI。

## 镜像与扫描覆盖

[应用镜像记录](application-image-security.json) 分别列出包扫描与真实运行时检查。旧CPython和JVM未被原Trivy包扫描识别，不能根据原0告警推断已修复；Python签名源码、受损CA拒绝/合法TLS和native依赖闭包单独实测，[Java完整JRE文件指纹与native/CPE门禁](native-jvm-ci.json) 也独立核验。新鲜数据库另发现Web的curl/libcurl高危，已正常升级并复扫，不忽略原告警。中低危数量和识别范围保留在原始记录。

[MinIO固定归档门禁](minio-ci-integration.json) 只接受获批的同一OCI内容，原始1HIGH/0CRITICAL与精确FIXED1分开保留。[Grafana 最终新制品](grafana-artifact-governance.json) 为 `9a68d3a7…`，musl 已实际修复，五个 ELF 的编译依赖图和二进制排除了公告指定的七个旧 OpenPGP 包。完整新 subject 的18个目标/公告行获得独立人类授权，机器门禁分别报告 FIXED2、NOT_AFFECTED16；没有继承旧 `646b…` 的授权。Trivy 原始2CRITICAL/11HIGH/20MEDIUM/2LOW/5UNKNOWN、Grype 原始2HIGH/3MEDIUM/5UNKNOWN均保留，三条 BusyBox 中危未被豁免。[13段实际远程下载、整档重组及OCI校验](grafana-remote-transport.json) 已通过：849540608 字节全部来自远程分段，整档 SHA256 为 `02332c39f6daed922cd030fbcaa00e4949e07b1c157776a2ca60640327e4e59b`，未使用本地源归档补齐。原始短流失败、首次分段8成功/5失败及后续补齐的收据保留。Release 尚为草稿，正常远程加载必须实时核对已发布状态，缓存不能绕过；新增8个发布状态回归通过。私有草稿传输审计不等于发布完成或最终 SHA 的冷 GitHub CI 通过。授权到2026-11-25失效；新发现、失配、扫描失败或证据缺失均阻断，不把 Unknown 降为低危或把失败写成零。

[镜像备份包校验](backup-image-bundle.json) 在备份完成前、恢复查询本机镜像缓存前验证当前平台内容。历史2.456GB包的22个镜像根、232个必需 blob、166个实际解压 DiffID 通过，35个直接用例通过。非目标平台的缺失引用单独记录；这不是完整多架构包、空缓存导入或整栈冷恢复证明。日常冷备及独立项目恢复仍待部署窗口。

最新清理顺序以 [scope.json](scope.json) 为准：两个项目完成日常验收和最终 SHA 的 GitHub 门禁后，再由协调方核对最终保留集并发放单独的最终清理窗口。[原18容器清单](cleanup-plan.json) 仍只是冻结的条件预审，不在部署前执行。最终审查扩展到明确专属、无引用的旧镜像和废弃卷；保留备份文件、其他项目、共享或归属不明资源，禁止全局 prune、`down -v` 和强制删除。

## 付费与效果边界

[frozen-live-cases.json](frozen-live-cases.json) 冻结一个开发样本和一个留出样本、原始合成资料及禁止编造项。[live-policy.json](live-policy.json) 限制新增23次模型 HTTP、20次嵌入、0次搜索/重排，所有尝试共享 Linux 持久账本，累计 token 预留上限800000。不选择最好的一次结果，不因低实耗重置累计预留。

[两条真实供应商样本](real-provider.json) 已经完成实际浏览器上传、研究、异人审核账号发布。实际12次模型、4次嵌入，返回输入usage合计12974、输出1775，累计预留155718，未知usage为0。原Linux SQLite账本与传输逐次记录保留；它们不是人民币结算账单。两份报告都保留 `partial`：开发例记录向量不可用降级，原因尚未独立定位；留出例的覆盖检查仍列出整个问题待确认。事实片段保留范围、否定和未知备份期限，不通过改题、调门槛或择优重跑转成“全通过”。

CI 后续获得用户授权：所有新完整质量 CI 尝试合计最多515次模型 HTTP，保留历史843次记录及原有 token 总限。该授权与两条本地样本分账；冻结样本文件中“CI57次”的文字是授权前历史，当前 CI 策略以 [../../../eval/ci-budget-authorization.json](../../../eval/ci-budget-authorization.json) 及对应累计账本为准。必要预算下界不保证模型质量通过。普通 CI 和 Security 同一源码版本成功后，才允许开始付费质量调用。

两条新增合成样本及固定题通过均不能证明一般准确率、实际用户收益、HA、SLA或生产容量。[人工盲评交接包](blind-review/README.md) 不含真人评分；自动化审核账号不能冒充真人评审。

## 当前外部限制

用户明确决定“暂无目标，先完成其他工作”。真实异机恢复标记为 `deferred_by_user_not_verified`，不再等待地址；可配置备份、本机隔离恢复、受控告警与保留策略仍须完成。真实 SMTP 送达、目标用户 MFA 注册/恢复以及外部域名 TLS 须另有接入条件和证据，不能用只读配置检查代替实测。

日常服务仍按原编排运行。候选组件的成功结果不等同已部署，镜像某时点0个 HIGH/CRITICAL 发现也不等同零风险。

[Windows静默入口](windows-host-task-silence.json) 强制pythonw、隐藏全部备份子进程、显式当前用户Interactive/Limited，保留日志及真实退出码。10个直接用例和PowerShell语法检查通过，实际任务安装及日常单次备份仍待窗口。

[最新只读源码核验](candidate-source-fingerprints-v7.json) 从现有已停止容器读取实际文件，五个服务的88/29/31输入及Web7个产物均匹配；实际JAR也与原源码绑定结果相同。此前live-only v6因服务已停止返回0个服务，失败记录保留。候选和日常服务均观察到约2026-10-27 08:47上海时间已停止，原因未在本次审查中建立；本次没有启停服务，也不声称当前可用。

[最终保留与清理初审](final-retention-plan.preliminary.json) 扩展到43个有原创建依据的条件容器、107个仅发现线索待归属核对的容器、74个镜像及76个卷的逐项审查。输入是旧快照，不是可立即执行的删除清单；最终部署与两项目CI通过后必须刷新精确保留集和全部引用，再取得独立最终清理窗口。
