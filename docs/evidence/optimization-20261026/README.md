# 2026-10-26 架构、可靠性与安全优化证据

当前阶段：`CI_CORRECTIONS_LOCALLY_VALIDATED_FINAL_REVIEW_AND_CI_PENDING`。此前日常 `0017_product_governance` 的本地闭环是已取得的历史证据；最终源码 GitHub 门禁、后续修正的定向日常切换及另获许可的清理尚未完成，不应据此把当前项目标为最终安全验收通过。逐项状态以 [scope.json](scope.json) 和各具名收据为准。

第一轮精确提交 `6bec3f00d883f0de0aac89b29d3a30cd7325fdf3` 的 CI/Security/Quality 均有失败记录；质量作业在付费前置条件失败，封存账本仍为历史843次、新增0次，后续515次共享授权未消耗。失败包括实际 API 跨空间错误语义、测试输入/启动夹具、Python3.12预算AST证明差异、源依赖和公开示例私钥打包。相关代码与定向修正已准备或验证，最终替代提交及其所有 GitHub 门禁仍待完成。

历史912文件扫描的默认开发依赖抑制和[升级Vite前的954文件完整快照](source-full-devdeps-pre-vite-repair.json)均保留：后者为3个HIGH、14个UNKNOWN、13个MEDIUM、28个LOW及0个secret，不能当修复后或最终clean SHA结论。Vite的[两份依赖输入](frontend-vite-security-fix.json)已升级8.2.4，[正常Linux Web构建](vite-normal-linux-web-build.json)一次成功，包含实际vue-tsc和vite8.2.4编译。新候选`b4725a15…`的31项源文件及7项dist指纹一致，71个runtime包的完整扫描未发现漏洞或secret；这不表示runtime扫描覆盖了已移出最终镜像的Vite开发依赖，最终既有Security仍执行fresh源码扫描。未追加host构建、本地全量复扫或业务/角色/备份重跑。

原来的两个Grafana主模块HIGH现采用[精确编译事实技术路线](source2-technical-route.json)：固定源码036450…、原7e0…绑定、4200包完整图和同一9a68…主ELF的523包库存均排除Tempo。仅两条精确tuple分类为`NOT_APPLICABLE_TO_EXACT_BOUND_MAIN_BUILD`，原始HIGH仍保留，依赖版本未声称已修复；这是[独立根复核](../../../deployment/security/grafana-source2-technical-review.json)，不伪造新人类风险接受或继承runtime18结论。实现已按精确8文件复核应用，22个直接用例及改动文件Ruff通过，最终CI仍未运行。

验收按最新用户要求收敛为基本功能可用、既有CI通过和受影响日常路径回读。源码漏洞/secret仍保持原HIGH、CRITICAL门槛，完整原始M/L/UNKNOWN如实保留，不标FIXED或不受影响；未发布source16/runtime5严格提案不接入交付，也不扩为16/21项新审批。两份Milvus示例/测试模块的独立只读解析失败仍保留，不能冒称独立编译或测试通过；既有通过的业务、角色、备份及恢复不重复运行。

本轮4个候选的首批完整扫描均为0 HIGH/CRITICAL、0 secret，但仍有11个UNKNOWN。官方核对确认其中Python的DSA-6572-1是实际受损CA事件，旧候选默认OpenSSL/HTTPX信任集合实测仍含两枚相关信任根；仅构建阶段APT升级没有把完整CA闭包带入最终镜像。已实修完整CA闭包及匹配包记录，并将etcd改为精确获批的受支持Debian13基础系统与真实时区数据。新Python候选`c57a4dbe…`通过实际默认信任、旧根拒绝、合法TLS、native ABI和exec健康检查；etcd候选`ed0bca6d…`通过原生put/get、权限、时区及一次Milvus兼容查询。2026-10-27本轮新库的全等级扫描分别保留Python31个MEDIUM/10个LOW/0 UNKNOWN，etcd23个MEDIUM/11个LOW/3个Go UNKNOWN；二者HIGH/CRITICAL和secret均为0。旧OS相关UNKNOWN已消失，3个Go UNKNOWN仍保留，未判定不受影响，也未被降级或消除；其不是新增全UNKNOWN验收门禁。源码、图与包内容事实、候选运行验证、日常生效状态和最终CI分别登记，候选安全修复尚未日常切换或最终验收。

DeepResearch本轮修正候选及一次正常Linux Web构建均已完成，当前无在途构建或扫描，仅保留一个永不启动的Web审计容器。窗口待协调方最终复核后释放；尚无commit/push、新CI、日常切换、清理或付费许可。构建前后DR日常25服务身份、镜像、启动时间和挂载连续性未变；另一个项目的容器启动时间变化已由用户确认是主动启动，不作为本项目问题或后续诊断。此前日常25个服务中21个长期服务及4个已完成一次性任务、五角色、旧数据保留、监控、原生静默备份和同机独立恢复的结果按原收据保留；新修正不能自动继承其源码/镜像身份。[原日常部署](daily-deployment-acceptance.json)、[升级前备份及恢复](daily-old-backup-restore.json)、[升级后独立恢复](daily-postupgrade-backup-restore.json)是对应版本历史验证。后续只对受影响服务执行另获许可的切换与必要回读。

## 架构判断

Java 控制身份、权限、任务接纳、发布审核和最终业务状态；Python 执行研究图、检索、解析及受控恢复，PostgreSQL 保存业务事实。这个划分适合当前单机内部试用。无需为了“微服务化”再拆出更多独立部署单元。

原来的主要不足在于跨语言边界和故障状态：恢复资格可能不一致，首次扫描缺少完整恢复闭环，SQL 删除与检查点/对象存储清理不能原子提交，私有历史资料必须在撤权后再次过滤。修复采用共享版本契约、尝试号隔离、持久清理记录和逐次授权，不以更多模型节点替代这些控制。

文档解析迁入一次性受限进程；大语料关键词召回改为数据库派生词索引；范围确认、资料集合、费用和公开来源重核验拆成独立界面/服务。仍有较长的 `Runtime`、研究图与工作台文件；当前采用稳定契约下的渐进拆分，未宣称结构债务全部消失。

`RunCleanup` 进一步分离事务内删除准备和事务外可重试清理，核验目标采用类型契约，资料界面采用空间代次隔离异步响应。成员管理的真实浏览器验收另发现保存刷新会清空下一条输入，已加入提交保护、输入身份和空间响应校验。

## 可核对的实现与验收

| 项目 | 当前证据 | 尚待完成或证据边界 |
| --- | --- | --- |
| 0 基线与共同交付 | 冻结输入、分项账本、独立候选项目；[日常部署](daily-deployment-acceptance.json)、[静默备份](daily-scheduled-backup.json)、[升级后恢复](daily-postupgrade-backup-restore.json) | 本地闭环完成；最终 SHA 与 CI、清理须另获许可 |
| 1 恢复与异常分类 | [reliability.json](reliability.json)、[recovery-and-sources.json](recovery-and-sources.json) | 一次公开任务出现约266秒停顿后自动恢复，未复现；原因仍在核查 |
| 2 费用治理 | [governance.json](governance.json)、[usage-postgres.json](usage-postgres.json) | 运维探测与静态检查区分；已知实耗、未知预留分开；真实供应商验收另记 |
| 3 上传扫描恢复 | [reliability.json](reliability.json)、[dependency-faults.json](dependency-faults.json)、[browser-failures.json](browser-failures.json) | 真实 ClamAV 故障暴露的退避问题已修复并重验；MinIO 断服返回受控503，同内容重传复用记录 |
| 4 解析资源边界 | [parser.json](parser.json) | Windows 与 Linux 有针对性验证；不等同完整恶意代码沙箱 |
| 5 删除与保留 | [reliability.json](reliability.json)、[reliability-postgres.json](reliability-postgres.json) | 实际 PostgreSQL 并发、检查点与文档向量故障重试通过；来源不明的独立语义记忆物理保留，失去原任务后拒绝上下文召回 |
| 6 服务最小权限 | [privileges-isolated-v3.json](privileges-isolated-v3.json)、[storage-isolated.json](storage-isolated.json)、[新鲜库供应商扫描](vendor-fresh-scan.json)、[精确 Grafana 门禁](grafana-artifact-governance.json) | 本地普通镜像扫描与获批制品定向判定分别验证；首轮精确SHA Security已失败并保留；最终替代SHA仍待验证，原始中低危和 Unknown 不删除 |
| 7 检索容量边界 | [retrieval.json](retrieval.json)、[维护就绪边界](lexical-maintenance-readiness.json)、[实际迁移](daily-deployment-acceptance.json) | 117个词法标记保留，1个空间不存在且无成员的孤儿片段明确延后，`all_eligible_indexed=false`；不是全数据索引完成或通用容量承诺 |
| 8 研究范围确认 | [scope-intake.json](scope-intake.json)、[roles-and-scope.json](roles-and-scope.json) | 确定性保守触发规则，不保证识别所有模糊问题 |
| 9 资料与历史权限 | [acl.json](acl.json)、[roles-and-scope.json](roles-and-scope.json) | 当前权限重查覆盖检索、来源、记忆、报告与导出；异人角色由自动化浏览器操作 |
| 10 公开来源时效 | [source-revalidation.json](source-revalidation.json) | 来源正文变化不是事实错误证明；浏览器正文和模型为受控夹具 |
| 11 跨语言契约 | [reliability.json](reliability.json) | 共享冻结样例、严格版本/字段、尝试号和数据库写入边界 |
| 12 模块与运维 | [boundary-review.json](boundary-review.json)、[infra.json](infra.json)、[日常监控回读](daily-monitoring-readback.json)、[面板浏览器](daily-grafana-panel8-browser.json)、[静默任务](daily-scheduled-backup.json) | 本地运维与同机恢复已验收；异机、空缓存及长期无人值守可靠性不由单次结果证明 |

[document-lifecycle-roles.json](document-lifecycle-roles.json) 验证实际文件上传、替换、影响分析、报告修订、独立审核账号发布、只读访问和撤权。五角色、终态恢复及公开来源修订使用真实 OIDC、Java/Python、PostgreSQL、Redis、MinIO、Milvus、ClamAV；模型、嵌入或网页正文的替换点明确标为夹具。没有全程拦截浏览器业务 HTTP。

[统一候选验收](unified-candidate.json) 已切换正式完整构建的Python3.11.18、签名Java21.0.13+9及更新后的数据/身份组件；[五个应用服务的源码指纹](candidate-source-fingerprints-v5.json) 均匹配。重新验证五角色、Java恢复、资料生命周期和真实扫描/对象存储故障路径，无新增供应商调用。升级前保留11个合成数据卷冷快照，另以只读SQL核对[163条选定旧行和11份旧正式快照](upgrade-old-row-preservation.json)，均保留且固定字段/正文摘要一致。这些结果不是完整存储恢复或日常部署证明。

[日常五角色验收](daily-five-role-acceptance.json) 取得79项功能检查、7项拒绝和17张私密截图，新增供应商调用为0。原脚本的 `Response.ok()` 预检错误和 Keycloak 未保留自定义属性导致的收尾失败均保留；后者按实际创建的5个精确用户ID独立核对并删除、确认404，没有重跑已经通过的业务检查。[迁移后](daily-sql-post-schema-preservation.json)及[五角色后](daily-sql-post-runtime-preservation.json)对15张表原2132行、11844个字段逐项比较，旧行/字段未变，允许新增18行合成数据；[S3旧版本核对](daily-s3-preservation.json)的5个版本、1243982字节正文摘要全部一致。这些是选定存量数据保留证明，不是全库恢复证明。

[日常依赖与解释器回读](daily-native-readback.json) 验证实际 ClamAV clean/EICAR、Milvus两集合、Python3.11.18和Java21.0.13。[Grafana精确管理员修复](daily-grafana-admin-reconciliation.json)保留旧卷冷副本，仅重置id1密码并通过stdin传入，实际 `/api/user` 返回200且身份为id1/admin；[监控回读](daily-monitoring-readback.json)确认5个目标、3个数据源健康及panel8轴配置。[实际浏览器面板](daily-grafana-panel8-browser.json)已检查真实渲染和坐标轴，私密截图摘要保留；零值曲线不当作识图准确率或故障率结论。

失败尝试、修复前后结果和私密原始材料的摘要分别保留。测试脚本错误、已确认缺陷、尚未定位的停顿与供应商扫描失败不混写成“全部通过”。仅重测修改和直接影响路径，完整检查交最终 GitHub CI。

## 镜像与扫描覆盖

[应用镜像记录](application-image-security.json) 分别列出包扫描与真实运行时检查。旧CPython和JVM未被原Trivy包扫描识别，不能根据原0告警推断已修复；Python签名源码、受损CA拒绝/合法TLS和native依赖闭包单独实测，[Java完整JRE文件指纹与native/CPE门禁](native-jvm-ci.json) 也独立核验。新鲜数据库另发现Web的curl/libcurl高危，已正常升级并复扫，不忽略原告警。中低危数量和识别范围保留在原始记录。

[MinIO固定归档门禁](minio-ci-integration.json) 只接受获批的同一OCI内容，原始1HIGH/0CRITICAL与精确FIXED1分开保留。[Grafana 最终新制品](grafana-artifact-governance.json) 为 `9a68d3a7…`，musl 已实际修复，五个 ELF 的编译依赖图和二进制排除了公告指定的七个旧 OpenPGP 包。完整新 subject 的18个目标/公告行获得独立人类授权，机器门禁分别报告 FIXED2、NOT_AFFECTED16；没有继承旧 `646b…` 的授权。Trivy 原始2CRITICAL/11HIGH/20MEDIUM/2LOW/5UNKNOWN、Grype 原始2HIGH/3MEDIUM/5UNKNOWN均保留，三条 BusyBox 中危未被豁免。[13段实际远程下载、整档重组及OCI校验](grafana-remote-transport.json) 已通过：849540608 字节全部来自远程分段，整档 SHA256 为 `02332c39f6daed922cd030fbcaa00e4949e07b1c157776a2ca60640327e4e59b`，未使用本地源归档补齐。原始短流失败、首次分段8成功/5失败及后续补齐的收据保留。[两份发布来源回读](published-artifact-origin.json)于2026-10-27再次读取官方Release与tag：固定release均已发布为prerelease，tag仍指向6bec3f00d883f0de0aac89b29d3a30cd7325fdf3，没有force-retag。正常远程加载仍实时核对发布状态，缓存不能绕过。新的应用提交须证明该提交是祖先、128个冻结文件在祖先/当前提交/index/工作区逐字节相同；这不称为在新应用提交重建过制品。先前私有草稿传输审计与此发布元数据回读均不等同最终源码的冷GitHub CI通过。授权到2026-11-25失效；新发现、失配、扫描失败或证据缺失均阻断，不把 Unknown 降为低危或把失败写成零。

[镜像备份包校验](backup-image-bundle.json) 在备份完成前、恢复查询本机镜像缓存前验证当前平台内容。历史2.456GB包的22个镜像根、232个必需 blob、166个实际解压 DiffID 通过，35个直接用例通过。非目标平台的缺失引用单独记录；这不是完整多架构包或空缓存导入证明。另有[本窗口升级前旧版备份/恢复实测](daily-old-backup-restore.json)。

[首次实际静默备份失败](daily-scheduled-backup.json)的退出码1保留，未更新成功心跳；[25个根的完整普查](daily-backup-all-roots-survey.json)发现254个现存blob摘要无误，13个缺失引用全部属于Loki。按[原始OCI相同字节补齐](daily-backup-content-rehydration.json)后，原镜像身份及校验器门槛不变，定向Docker导出通过20个blob、18个DiffID校验；旧失败包仍为失败。第二次实际任务退出码0，新备份17份卷归档、25个镜像根、267个blob、195个DiffID全部通过；[升级后独立恢复](daily-postupgrade-backup-restore.json)实测227.61秒通过，23个恢复编排容器已停止并保留，34个带创建归属证明的helper、17个新卷和隔离网络保留。

恢复验证涵盖当前ACL表和默认文档策略、2个OIDC token、3项权限边界、1份报告与3份引用快照、2项原生向量检索和2项ClamAV检查。新研究仅接纳入队后显式取消，上游禁用，没有证明模型执行完成。源日常集合为空，因此没有证明恢复非空私有集合/授权数据；此前候选私有集合验收另记。本机已有不可变镜像缓存，包先于缓存查询验证；这仍不是异机、空daemon、多架构、HA或普遍RTO承诺。

最新清理顺序以 [scope.json](scope.json) 为准：两个项目完成日常验收和最终 SHA 的 GitHub 门禁后，再由协调方核对最终保留集并发放单独的最终清理窗口。[原18容器清单](cleanup-plan.json) 仍只是冻结的条件预审，不在部署前执行。最终审查扩展到明确专属、无引用的旧镜像和废弃卷；保留备份文件、其他项目、共享或归属不明资源，禁止全局 prune、`down -v` 和强制删除。

## 付费与效果边界

[frozen-live-cases.json](frozen-live-cases.json) 冻结一个开发样本和一个留出样本、原始合成资料及禁止编造项。[live-policy.json](live-policy.json) 限制新增23次模型 HTTP、20次嵌入、0次搜索/重排，所有尝试共享 Linux 持久账本，累计 token 预留上限800000。不选择最好的一次结果，不因低实耗重置累计预留。

[两条真实供应商样本](real-provider.json) 已经完成实际浏览器上传、研究、异人审核账号发布。实际12次模型、4次嵌入，返回输入usage合计12974、输出1775，累计预留155718，未知usage为0。原Linux SQLite账本与传输逐次记录保留；它们不是人民币结算账单。两份报告都保留 `partial`：开发例记录向量不可用降级，原因尚未独立定位；留出例的覆盖检查仍列出整个问题待确认。事实片段保留范围、否定和未知备份期限，不通过改题、调门槛或择优重跑转成“全通过”。

CI 后续获得用户授权：所有新完整质量 CI 尝试合计最多515次模型 HTTP，保留历史843次记录及原有 token 总限。该授权与两条本地样本分账；冻结样本文件中“CI57次”的文字是授权前历史，当前 CI 策略以 [../../../eval/ci-budget-authorization.json](../../../eval/ci-budget-authorization.json) 及对应累计账本为准。必要预算下界不保证模型质量通过。普通 CI 和 Security 同一源码版本成功后，才允许开始付费质量调用。

两条新增合成样本及固定题通过均不能证明一般准确率、实际用户收益、HA、SLA或生产容量。[人工盲评交接包](blind-review/README.md) 不含真人评分；自动化审核账号不能冒充真人评审。

## 当前外部限制

用户明确决定“暂无目标，先完成其他工作”。真实异机恢复标记为 `deferred_by_user_not_verified`，不再等待地址；升级前后同机隔离恢复已有分别实测，受控告警与保留策略按各自收据判定。真实 SMTP 送达、目标用户 MFA 注册/恢复以及外部域名 TLS 须另有接入条件和证据，不能用只读配置检查代替实测。

此前日常版本已完成对应升级与验收；本轮CI修正和CA/基础系统修复仍按本文首段的当前阶段执行。候选与日常证据分别列出，0个HIGH/CRITICAL发现不等同零风险。

[Windows静默入口](windows-host-task-silence.json) 强制pythonw、隐藏备份子进程、显式当前用户Interactive/Limited，保留日志及真实退出码。除10个直接用例和PowerShell语法检查外，[实际原生任务](daily-scheduled-backup.json)的第二次备份退出码0，205次进程后代窗口采样未观察到可见窗口；首次失败任务同样完整保留。备份前后实际探测任务均退出0，未伪造备份成功，也未用容量探测续签最后验证时间。永久计划为每日03:30备份、每5分钟探测，临时维护许可已还原为03:15—04:30。采样不能排除两次采样间所有瞬时窗口，单次验收不证明长期无人值守运行。

[候选停机时源码核验](candidate-source-fingerprints-v7.json) 从当时已停止容器读取实际文件，五个服务的88/29/31输入及Web7个产物均匹配，实际JAR与原源码绑定结果相同；此前live-only v6返回0个服务的失败记录保留。约2026-10-27 08:47上海时间的停机观察是窗口前历史状态，原因未在该次审查中建立；当前日常状态以[实际部署](daily-deployment-acceptance.json)及[日常运行源码](daily-runtime-source.json)为准，不由旧候选快照推断。

[最终保留与清理初审](final-retention-plan.preliminary.json) 扩展到43个有原创建依据的条件容器、107个仅发现线索待归属核对的容器、74个镜像及76个卷的逐项审查。输入是旧快照，不是可立即执行的删除清单；最终部署与两项目CI通过后必须刷新精确保留集和全部引用，再取得独立最终清理窗口。
