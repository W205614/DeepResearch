# 本地生产化验收证据（2026-11-07）

这些是本次执行的脱敏结果，不是生产SLA或目标云平台批准。模型连通性使用真实OpenAI；负载、故障与恢复使用无付费模型替身和真实基础组件。API密钥、JWT、数据库凭据、供应商原始响应、业务原文和备份包不进入此目录。

| 记录 | 内容与边界 |
| --- | --- |
| [OpenAI](openai-smoke.json) | 文字、合成图片、Responses联网搜索、单条3072维嵌入，各一次HTTP，禁重试，四次全部通过；不是完整模型质量评测 |
| [历史检查](history-inspection.json) | 停止状态日常卷的独立只读副本；118候选片段，无重建，无日常嵌入切换；Token仅保守估算 |
| [故障演练](faults.json) | 双Java/Agent/研究Worker，Redis断连保留授权读取并503拒写，单副本退出，Worker强制中断后唯一结果恢复 |
| [恢复快照](recovery-snapshot.json)、[还原](recovery-restore.json)、[核验](recovery-verify.json) | 独立数据库/Bucket/向量前缀；真实上传、研究、异人审核发布；核对54张表、原文、向量、权限、引用、报告SHA和下载 |
| [30分钟容量](capacity.json)、[运行镜像](runtime-baseline.json) | 50账号、10在线、500资料/50000片段/3072维；17209请求，601任务收敛，控制P95 210.95ms；原检索P95 24652.72ms如实保留 |
| [检索诊断](retrieval-profile.json)、[缓存修复短测](cache-retest.json)、[短测镜像](cache-retest-runtime.json) | 同一资料及向量ID集合；有效缓存前置，183.02秒3283请求0错误，210任务收敛，检索P95 300.92ms；不是修复后的30分钟容量验收或冷缓存保证 |
| [账号治理](governance-live.json)、[管理概览](admin-overview-live.json) | 双Java/双Python实际停用、恢复、旧令牌失效、重新登录、交接保留作者、在途取消及迟到结果不发布；概览仅返回安全元数据 |
| [本地验证汇总](local-validation.md) | 测试覆盖、执行版本和仍待外部验收项；跳过不计通过 |
| [精确提交CI](github-c6acf98.json) | c6acf98完整CI四个Job成功、告警规则通过；Python1528/8跳过、Java61、前端33、真实组件5、企业浏览器22/14跳过；安全21成功/旧Grafana1阻断 |
| [证据字节保留](source-byte-preservation.json) | 三个精确路径的原字节、真实Git index与LF检出/loader独立核验；128冻结材料不变 |
| [首轮安全阻断](security-review.md) | 真实Security失败、精确补充审查边界与未授权候选流程；应用测试通过不代表安全放行 |
| [精确技术补充](security-supplements.json) | SQLite确修、MinIO原OCI与源码快照的离线重算及独立复核；旧raw/verdict保留，Grafana新增16项仍阻断 |
| [JVM系统zlib组件](jvm-zlib-upgrade.json)、[产品镜像](jvm-zlib-product.json) | 精确官方1.4-r0包、实际ELF和Java直接系统库/标准压缩回归；等字节官方JRE缓存运输产品构建通过，原URL运输与新鲜扫描另行核验 |
| [新增Tempo清单路径](source18-supplement.json) | 完整62目标/18 HIGH重算；实际main无Tempo、插件仅tempopb协议包，31新测试及106旧链回归通过；保留UNKNOWN和失败链，不授权候选制品 |

恢复演练在同一台本机Docker环境执行，复用隔离Keycloak的稳定身份，不代表IdP原生备份、异机恢复、托管备份策略或云端RPO/RTO已验收。演练专用工具镜像包含匹配的PostgreSQL17.11客户端；日常应用不取得恢复管理凭据。

容量数据通过真实上传、ClamAV、解析、SQL词法索引及Milvus入库产生。联调曾发现批量上传背压和PostgreSQL大语料词法查询参数类型错误：预置已节流，生产查询显式声明数值类型，并用真实PG验证权重计分。原500份资料/5万片段保留，复用前逐份校验原文SHA、成员、每文档片段及SQL/Milvus ID集合；不得用修改指纹或空集合绕过验收。

全量回归与最终专项结果见上述本地验证汇总；运行中、失败、跳过和中止都不能视为通过。新代码尚未通过目标云的TLS/OIDC全流程、工作负载身份、告警实际送达、异机恢复或新模型完整质量评测。日常容器未自动替换，本机备份仍按用户设置停用。
