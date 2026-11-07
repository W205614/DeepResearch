# 本地验证与交付边界

日期：2026-11-07。起点 `d57479e976c9230bea54e17bf8e8cea86a39c010`；增量 schema 到 `0019_embedding_migrations`。模型探测与隔离验收分账，未改写历史质量账本。

| 范围 | 实际结果 | 版本与限制 |
| --- | --- | --- |
| Python 全量回归 | 1391 passed、20 skipped，1757.12秒 | 缓存前置这一最后改动之前；20项跳过不计通过 |
| 最后缓存改动 | 29 passed，122.57秒；Ruff、编译检查通过 | database_lexical/rag_hardening；覆盖有效缓存免重算、撤权、中途撤权、真实资料替换与坏缓存 |
| 最后安全/预算检查 | 58 passed，22.90秒 | ci_budget_precheck、grafana_active_source_retirement、artifact_source_reuse；不执行付费评测 |
| Java | 完整包构建60项通过；最终只读概览变更9项相关集成测试通过并重新打包 | 测试有重叠，不相加为69项；真实PostgreSQL/Redis |
| 前端 | 33项单元测试通过，类型检查/构建通过；13个不同浏览器场景通过 | 其中最终管理页4项含移动布局、用量/未探测依赖、只读、权限入口；13不是一次全量浏览器运行 |
| 离线冻结基线 | `scripts/evaluate.py --verify` 通过 | 确定性替身，不能证明真实模型质量 |
| 部署材料 | Kubernetes生成器 `--check`、base Kustomize渲染、相关45项定向测试通过 | 无目标集群，不计为已部署；独立迁移Job与SA先于应用 |
| 观测 | promtool验证37条规则、3份配置、8组PromQL场景；备份转换7项测试通过 | 未验证告警实际送达或云备份成功 |
| 冻结证据 | 128文件及两个artifact发布tag保持原字节/身份 | 最后检查覆盖祖先、应用提交、index和worktree；不等于新镜像安全批准 |
| 首轮GitHub质量Job | Python 1408 passed / 8 skipped、Java61项、前端33项及构建通过 | 对应068c7d4；同轮组件/整栈和安全Job另有失败，不能写成整个CI通过 |
| 整栈问题定向修复 | 实际Nginx镜像下10项生命周期/平台管理浏览器测试通过；前端33项和构建再次通过 | 模拟身份地址与CSP一致；保存期间禁用原因输入，避免前一次异步刷新清空下一次原因；最终完整CI另行核验 |
| 排队契约回归 | 5项claim/intake/capacity相关测试通过 | 旧PG组件测试更新为独立平台队列上限；生产阈值未变，真实PG复验须看最终组件CI |
| SQLite固定代码补充 | 56项正反例通过，原始CI工件离线重算通过 | 仅119374/119379两个精确HIGH元组，实际已加载官方库哈希及源码补丁匹配；原始raw/旧锁不改，其他发现/secret仍阻断 |
| MinIO/源码分类补充 | 108项直接回归通过，原授权OCI/ELF及完整raw离线复核通过 | 保留原失败verdict；仅当前精确tuple和原合法subject，不能授权新制品或排除Grafana新增16项 |
| 日志时间 | UTC固定时间探测和现有观测测试通过 | 修正非UTC宿主机将本地时间误标Z的问题，运行容器默认UTC的业务行为不变 |
| JVM系统zlib | 官方1.4-r0实际ELF匹配，Java21 FFM直接系统libz及GZIP/Deflater三组roundtrip通过 | 0/64KiB/1MiB；组件与等字节JRE缓存运输的产品镜像均通过，255项JRE字节匹配；原URL运输与新鲜扫描须看最终CI，未加安全例外 |
| 新增Tempo清单路径 | 31项新直接测试、106项旧源码链回归和浅克隆修正单项通过，独立只读复核无阻断 | 完整62目标/18 HIGH和5新增UNKNOWN保留；14实际ELF/完整图/native对齐，main无Tempo、插件仅4个tempopb包；原失败链保留，不授权候选或风险接受 |

原始日志、截图、私密令牌、测试配置和逻辑备份包保留在本机忽略目录。GitHub检查按最终提交查看，未结束/跳过不能计为通过，本文件不预写远端结果。首轮[安全失败及独立候选边界](security-review.md)明确保留，补充技术验证不等于整体安全放行。

提交 `41aaaed` 的GitHub质量Job实际通过：Python1488 passed / 8 skipped、Java61项、前端33项及构建。真实组件Job的5项集成及57.96秒Worker恢复、Redis只读恢复和Milvus就绪恢复通过；同提交整栈Job当时仍运行。安全Job仍失败：新增清单路径的4条Tempo HIGH、未切换的旧Grafana运行制品、系统zlib的新CRITICAL分类。不能将质量/组件通过写成全部CI或安全通过，最终提交须重新核验。

提交 `d91f0ce` 的新鲜Security扫描中，JVM系统zlib修复通过；整体仍因源码证据字节和旧Grafana制品失败。质量Job为1495 passed、8 skipped、24 errors：三个新增JSON原证据为CRLF，Git入库转换成LF，严格SHA检查正确拒绝。修复仅对这三个精确路径声明保留字节，并显式重新入库原字节，不改review pin或门禁。真实Git入库/干净LF检出/完整loader回归通过，另一个代理独立验证实际暂存区全部29项、main4200包/Tempo605包及冻结128项；[字节保留回执](source-byte-preservation.json)不代表最终CI已通过。

修复后的 `c6acf98ca5d753b6981153f49108f43c62dbef0d` 已完成[完整CI](https://github.com/W205614/DeepResearch/actions/runs/42356591589)：Python1528 passed / 8 skipped、Java61项、前端33项及构建；真实组件5项通过，Worker恢复50.8秒、Redis只读恢复与Milvus就绪恢复通过；企业整栈与浏览器22 passed / 14 skipped。四个CI Job与告警规则全部通过，跳过项不计通过。[精确提交回执](github-c6acf98.json)保留各Job链接。Security为21个成功Job、1个旧Grafana运行制品失败；新候选仍须完整技术复核与独立精确授权，不能把CI成功写成生产放行。

## 真实组件与模型

- 四次真实OpenAI HTTP探测全部通过；之后没有追加真实模型请求。重排保持BGE/SiliconFlow。
- 双Java、双Agent、双研究Worker、双资料Worker，使用真实PostgreSQL、Redis、Milvus、S3、ClamAV、Keycloak。负载/故障/恢复均使用受控模型替身。
- 30分钟容量：[原始脱敏报告](capacity.json)。控制API符合既定P95/错误率门槛；原检索P95 24.65秒保留，601收敛结果包含证据不足，不是601份完整研究报告。
- 最后缓存优化：[183.02秒短测](cache-retest.json)，同一500资料/50000片段/3072维，3283请求0非预期错误、210任务收敛、检索P95 300.92ms。不是最后源码的30分钟重验或冷缓存保证；旧报告未覆盖。
- [故障](faults.json)：Redis故障9次历史读均200、3项写均503；单Java/Agent副本退出仍可读；强制Worker退出后5个任务52.52秒内全部唯一收敛。
- [治理](governance-live.json)：两种语言共4个API副本停用后403，恢复后旧令牌401，重新登录200；在途任务取消，352.54秒后结果仍未发布。交接和回交保留历史创建者与角色。
- [恢复](recovery-verify.json)：同机独立目标恢复54张表、1个对象、1个向量，核对权限、引用和正式报告SHA/下载。复用隔离Keycloak，不代表身份平台原生备份或异机恢复。

容量镜像采用维护过的本地依赖基础镜像加源码覆盖，版本见[原容量](runtime-baseline.json)与[最后短测](cache-retest-runtime.json)。没有将其描述为canonical发布镜像；目标发布需按仓库正式Dockerfile构建、扫描并验收。

## 仍待目标环境完成

真实云资源、证书/最小权限、工作负载身份、OIDC策略/MFA/SMTP、告警送达、托管备份及异机恢复；新模型完整质量门槛；最后源码在目标环境的30分钟容量验收。支持的协议内通过配置接入，厂商控制面操作不由应用伪造成功。

日常25个容器未自动替换或启动，业务数据卷未重置；用户停用的本机备份保持停用。历史118片段只读核算后未付费重建，日常嵌入端点与其原密钥保持原状，不能把新OpenAI密钥发往旧第三方端点。新的默认嵌入配置与迁移工具已完成，切换须另行批准重建额度并验收。隔离测试资源停止后保留，不自动删卷或清理业务证据。
