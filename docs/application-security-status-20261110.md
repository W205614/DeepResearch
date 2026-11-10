# 应用镜像的全级别安全复核

核对日期：2026-11-10。当前默认分支的 70 条 Code scanning 告警已逐项处置，见[原登记与证据](security-alert-review-20261110.md)。它们与下面的镜像扫描属于不同范围。Secret scanning 当前活动项为 0；Dependabot 页面显示未启用，不能写作扫描后为 0。

## 公开制品与尚未完成的门槛

本轮使用无凭据 Docker 配置，重新匿名拉取三个已发布 `v0.1.1` 的不可变 digest，核对 manifest、config 与解压层身份，再用固定 Trivy 0.75.0、空配置和空 ignore 执行全部严重级别的漏洞与秘密扫描。扫描器成功不代表漏洞为零。

| 制品 | 原始发现 | 当前结论 |
| --- | --- | --- |
| [Agent](container-packages.md) `732fad95…` | 3 HIGH、43 MEDIUM、15 LOW | 61 条、43 个 CVE；未完成全部级别未解决归零 |
| Backend `c12ef4e6…` | 0 | 本次扫描范围内未报告漏洞或秘密 |
| Web `9819c4a5…` | 0 | 本次扫描范围内未报告漏洞或秘密 |

Agent 的三条 HIGH 已有实际 SQLite 固定代码、已加载库摘要与定向回归证据，旧门槛只处理 HIGH／CRITICAL，不能推出其余 58 条已修复。[61 条逐项登记](../deployment/security/agent-image-all-severity-review-20261110.json)保留官方来源、固定版本、技术判断及仍需完成的证明；它不是发布放行名单。

原公开镜像仍包含需要更新的 glibc。对于没有官方固定版本的 [CVE-2026-115456](https://security-tracker.debian.org/tracker/CVE-2026-115456)，后续复核补齐了实际调用路径证据：普通浮点 `std::from_chars` 是 GNU 运行库中的出线实现；完整 515 个 ELF 无该函数的动态导入，8 个直接依赖 `libstdc++` 的调用者中，唯一动态查找明确为 dav1d 的固定 `__pthread_get_minstack`，PDFium 使用 LLVM libc++。这支持精确当前字节中的函数不在执行路径，不能扩展为未来 FFI、插件或所有已编译 C++ 代码均安全。没有通过删除导出函数或泛化 LOW 忽略来处理它。

后续发布必须重新扫描已经加载、且 portable config 身份匹配的实际归档，在推送前阻断全部严重级别与秘密发现。默认仍要求 raw zero；仅 Agent 可明确选择[固定的逐项技术复核](../deployment/security/agent-full-scope-20261110.json)，还必须实测完整 ELF／运行 Python 源码闭包、290 项官方文件、SQLite 加载与扩展、locale 和运行配置。新增记录、severity／package tuple、字节或配置变化均失败。默认失败结果与原始扫描保留，不凭清单中的 `security: true` 放行。

## 已落实的源码修复与验证边界

Agent 配方将 glibc 从 Debian 13 基础层的 2.41 更新为官方 `2.43-7`，同时安装严格匹配的 `libc-gconv-modules-extra`。保持 builder 工具链不变，在最终运行树提取完整官方文件，移除旧 gconv 树，并更新真实 control、md5sums 和版权。[来源锁](../deployment/security/agent-glibc-runtime-20261110.json)列出包及对应源码的 URL、长度和 SHA-256。

隔离候选已核对 290 个实际文件／软链的内容、模式和目标；CPython、NumPy、psycopg、httpx、Uvicorn、FastAPI 等导入及数值、压缩、内存 SQLite、TLS 信任库和两种 iconv 转换通过。这是兼容性探测，不是完整业务或日常升级验收。

本机完整源码构建两次因固定官方下载提前 EOF 失败，原摘要未改变。另用仓库实际 staging 脚本和相同官方包完成隔离构建、真实包元数据及上述原生探测。[工程提交 e86d3cb 的 CI](https://github.com/W205614/DeepResearch/actions/runs/42780070931)四项成功，[同提交 Security](https://github.com/W205614/DeepResearch/actions/runs/42780070403)的 Agent 实际源码镜像构建及原生检查成功，但 Grafana 因新发现失败，整体不能写成 Security 通过。它们不替代后续最终提交及实际发布制品的验收。

隔离 stager 原始复扫仍有 83 条（3 HIGH、57 MEDIUM、23 LOW）：新增 gconv 分包增加重复的 glibc 匹配，Debian 13 的数据也未自动识别 sid 固定版本。逐项门槛在该 stager 的真实 Docker 归档扫描与再次实测中通过：40 条具有官方固定代码证据，其余 43 条分别依据具体组件／执行路径缺失和明确上游非安全／争议契约处理，没有风险接受项。83 条原始记录与默认门槛失败结果均保留；未伪改 `os-release`、包记录或原始报告。这只适用于该隔离运行库，最终法律材料重封装后的镜像仍须重新执行门槛。

## Grafana 的独立运行依赖修复

同提交新 Grype 扫描增加了主程序内 Thrift 0.24.0 的五条 HIGH，以及 BusyBox wget 的三条 MEDIUM 匹配。主程序确实链接 Arrow／Thrift 读取路径，不能以没有 Thrift 服务端为由关闭。新的独立源码构建配方将主程序更新为官方 Thrift 0.25.0，实际编译、完整 4200 包图与原生模块比对通过，13 个插件 ELF 保持；BusyBox 在 HTTP 发送边界拒绝未编码控制字节、空格和 DEL，保留编码路径，并在原系统完成对照测试。

新的 `grafana-source-20261110` 是本地源码构建入口，未继承旧 OCI 的安全放行，也不授予新的第三方二进制公开分发许可。[完整技术规则](../deployment/security/grafana-source-20261110-profile.json)绑定实际组合镜像的全部文件、14 个 ELF、4200 包主程序编译图及全部扫描记录。正常门槛实际通过：Trivy 原始 40 条（2 CRITICAL、11 HIGH、20 MEDIUM、2 LOW、5 UNKNOWN）、Grype 原始 10 条（2 HIGH、3 MEDIUM、5 UNKNOWN），未解决适用项为 0；两份原始报告均未改写。22 条新增中低级别记录中，21 条对应已存在的官方修复实现，另一条限于未包含的 Enterprise 产品，不能称本轮新修了 21 处代码。

[审阅 A](../deployment/security/grafana-source-20261110-evidence/review-runtime-a.json)与[审阅 B](../deployment/security/grafana-source-20261110-evidence/review-runtime-b.json)分别记录实际载荷、原始报告和拒绝反例检查，并披露各自此前贡献；它们是 AI 技术审阅，不是人工审查或二进制分发批准。第二位审阅者独立重跑 BusyBox 50 项边界及 53 项原系统对照测试，均通过。隔离 Grafana 实际启动、数据库健康、版本和 13 插件接口检查也通过，未接触日常数据。

新增源码证据纳入第三轮完整工程快照扫描：[A3 事实](../deployment/security/grafana-source-20261110-evidence/source-a3-technical-facts.json)有 79 个目标、71 条原始记录、secret 0；每个包库存摘要与完整告警元组均与上一轮一致，71 条技术证明复核通过。原始／候选旧门槛仍分别失败 24／22 条，保留其完整结果。A3 是私有工作快照预扫，扫描后第三方说明只更新核查日期；最终工程提交和默认分支的实际 CI 仍须另行通过，不能以预扫代替。

原 `v0.1.1` Packages、Tag 与附件保持不可变，原 25 个日常容器保持停止。本轮日常基线已只读记录 14 张业务表摘要，待恢复研究、资料索引和研究 Outbox 聚合均为空；检查后恢复全部停止，没有供应商调用。源码修复、候选兼容性、公开发布与日常升级验收分别记录，不能相互替代。后续须完成最终源码门槛、同提交 CI、最终应用制品和业务验证，才可按原 Registry／可见性发布单个新版本；不得覆盖现有版本。

## 干净 CI 的权限差异与增量修正

工程提交 `aff45202fbcaeaf911f2f23f32d449efa8c4587c` 的完整源码安全作业实际通过，79 个目标、71 条原始记录，正式门槛未解决 0。新增两份 Go 清单产生的 6 条告警（#153–#158）按真实编译图中受影响包缺失的依据关闭，逐条读取确认后默认分支活动 Code scanning 为 0；[逐项记录](../deployment/security/additional-source-alert-review-plan-20261110.json)保留精确源码提交、包版本、告警及证据链接。

该提交的 Security 整体仍失败：两次干净 runner 的 Grafana 构建虽有相同的 85 项输入摘要、14 个 ELF 和运行配置，完整文件清单因权限差异被严格门槛拒绝。Windows 传入的 66 个新增源码、许可证及归档文件为 `0755`，Linux Git 检出为 `0644`。配方现只固定这两棵材料树和单个来源锁文件的权限；两个可执行文件、继承的插件清单、所有文件内容及其他清单字段保持。[实际修正构建的完整清单](../deployment/security/grafana-source-20261110-evidence/runtime/canonical-file-modes-20261110.json)与两次干净 CI 的 `d0707a9f…` 精确相同。没有忽略模式字段或放松门槛。

CI 同时保留完整文件清单作为 JSON 诊断证据，不上传新第三方镜像二进制。普通版本的候选归档标签绑定源码 SHA 前 12 位，避免修复轮次覆盖此前本机候选；公开 Registry 仍只使用正式版本号，历史 artifact 规则保持。修正后的最终工程 CI、应用候选及公开发布验收尚待完成，不能把上述权限复现写成最终 CI 已通过。
