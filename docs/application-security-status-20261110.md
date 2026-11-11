# 应用镜像的全级别安全复核

核对日期：2026-11-11。当前默认分支的 70 条 Code scanning 告警已逐项处置，见[原登记与证据](security-alert-review-20261110.md)。它们与下面的镜像扫描属于不同范围。Secret scanning 当前活动项为 0；Dependabot 页面显示未启用，不能写作扫描后为 0。

正式交付现为 [`v0.1.3`](https://github.com/W205614/DeepResearch/releases/tag/v0.1.3)，源码与分发提交 `78a19df9dfa6f072af6ebe40ff99cf164d0c4ff8`。[CI](https://github.com/W205614/DeepResearch/actions/runs/42968387539) 四项及 [Security](https://github.com/W205614/DeepResearch/actions/runs/42968387608) 二十二项全部成功，[Packages](https://github.com/W205614/DeepResearch/actions/runs/42977626649) 已实际发布。三个公开镜像完成匿名拉取、完整 config／层、NOTICE 与全级别扫描核验：Agent 原始 88 条、精确适用未解决 0，Backend／Web 原始 0，三个制品 secret 均为 0。最终 Web 已扁平化并更新法律与源码索引，没有发布下文的中间修复镜像。

新源码分析实际报告四条高危／严重结果，四条精确技术复核通过；两条探针固定 IV 告警因真实代码修复自动标记为 fixed，没有 dismiss。默认分支 Code scanning 活动项为 0；历史告警、原始 SARIF 和失败尝试保留。隔离核心四个浏览器用例全部通过，使用受控模型夹具。日常验收实际更新九个应用角色、Grafana 和两个 PostgreSQL 角色，其余十三个容器身份保持，十四张业务表逐行摘要、十六个卷、九个网络及配置保护项保持，最后恢复二十五个容器全部停止。未追加付费 API 调用：两次官方探针沿用同一未变化 Agent 制品的 2026-11-10 实际结果。

本页下面按阶段保留当时的阻断、待验和失败；这些历史文字不表示上述最终交付仍未完成。镜像 digest、组件来源和新旧对应源码获取方式见[容器 Packages](container-packages.md)。Grafana 与 PostgreSQL 的本机验收不等于公开分发完整第三方基础栈；本轮没有新增其公开二进制镜像。

## 2026-11-11 增量复核与真实修复过程

[`v0.1.2`](https://github.com/W205614/DeepResearch/releases/tag/v0.1.2) 已公开，源码 Tag 为 `488550ecb61e7f369dd4967c7fa35e4c64369e75`；该提交的 [CI](https://github.com/W205614/DeepResearch/actions/runs/42827369515) 全部 4 项及 [Security](https://github.com/W205614/DeepResearch/actions/runs/42827369414) 全部 22 项成功。其 1,227,806,395 字节对应源码已实际匿名完整取回，全部 74 分片、整包 SHA-256、索引成员与嵌套归档通过，原始断流及分段恢复记录保留。

随后新官方漏洞库增加了告警。[Packages 作业](https://github.com/W205614/DeepResearch/actions/runs/42953274265) 在精确元组检查处失败，尚未执行 Registry 推送。本机使用同一候选与最新官方库独立复现，不能把旧库通过写成新库通过。这个云端失败的原始扫描当时只留在临时 runner，已上传的是失败裁决和日志；发布器现补正为在确认扫描完整且 secret 为 0 后保存未改写的公开 raw，即使后续技术复核失败也保留诊断。含 secret 的 raw 仍不公开。定向反例还复现了 raw-zero 初步结果可能在技术复核中断前提前写入通过状态的问题；现仅在完整技术复核结束后记录通过，`KeyboardInterrupt` 与 `RuntimeError` 均保留失败收据并传播，不执行推送。

Agent 原 83 条全部保留，新增 5 条 OpenSSL `UNKNOWN`，合计 88 条（3 HIGH、57 MEDIUM、23 LOW、5 UNKNOWN）。[新增逐项证据](../deployment/security/agent-openssl-20261111-evidence/new-findings-source-review.json)区分 Debian 系统库、cryptography 和 psycopg 的私有实现，核对 515 ELF、5972 Python 文件、实际 TLS method、真实 nonce／EC recipient 边界及当前调用方。新正式门槛实际通过，适用未解决 0、secret 0；默认 raw-zero 仍失败。原始级别和上游“包仍受影响、未发行固定版”的事实保留，结论仅为这五个触发路径不在精确当前应用中，不是 OpenSSL 已修复。

Web 的新库结果是 libxslt 四条告警（2 HIGH、1 MEDIUM、1 UNKNOWN）。[官方签名 `1.1.43-r4` 更新](../deployment/security/web-libxslt-20261111.json)真实替换原生实现和 APK 元数据，完整运行树只有五个预期路径变化，71 个包中仅该包升级，八个静态前端文件与运行配置保持。四补丁按准确 APKBUILD 顺序零 fuzz 应用；实际 XSLT／EXSLT、Nginx 配置和静态文件 HTTP 检查通过。修复中间镜像全级别 raw 与 secret 为 0；最终镜像仍须扁平化、更新法律／源码索引、复扫并完成整栈验收，不能直接发布继承旧层的中间镜像。

Backend 使用最新漏洞库及最新 Java 识别库复扫，raw 与 secret 为 0。另按已知 OpenSSL 公告补核未被 Alpine 库报告的路径：[Backend 证据](../deployment/security/backend-openssl-20261111-evidence/review.json)保留其确实存在的通用 OpenSSL CLI，核对 91 ELF、97 JAR 全部 26717 条目、61 业务 class、34 源码指纹及真实 JDK TLS；[Web 证据](../deployment/security/web-libxslt-20261111-evidence/openssl-configured-route-review.json)保留已编译的 HTTP/3 及 ACME CMS 调用模块，核对当前实际 HTTP80、无 UDP、无动态模块映射的进程。两者只得出当前配置／业务调用路径不适用，未声称整个镜像没有相应实现。手工 CLI、新 provider／JNI、TLS／QUIC／ACME 配置或入口改变均须重新评估。

Web 发生真实二进制修复，因此使用新修复版本 `v0.1.3`，不移动或覆盖已公开的 `v0.1.2`；这不是为验证轮次创建版本。未变的 Agent／Backend 只在 config 与所有实际层完全一致时复用，原 OCI 来源、NOTICE 与两次真实官方 API 记录保留原身份。新版本引用固定旧完整源码，再补 r4 完整源码与新工程归档，明确 reference 模式，不把小补充包称为自包含所有旧材料。最终工程 CI、组合核心、实际 Packages 拉取和日常升级属于各自验收收据，不能由本节本地成功替代。

Grafana 对同一已验收镜像另核对上述五项 OpenSSL 公告：[当前路径证据](../deployment/security/grafana-source-20261110-evidence/openssl-known-paths-review.json)覆盖完整 97 ELF、14 个 Go 制品及其编译图、实际 curl 能力、入口和配置，以及只读挂载得到的当前两项浏览器插件清单。库仍受影响，没有称作二进制修复。两个 AI 代理分别交叉核对实际字节、完整清单和调用路径，再对新增证据及发布器修复后的精确 subject 作增量复核；这不等于独立人工审查或第三方二进制重分发批准。原审查、失败与原始扫描保留，不把旧 subject 的结论直接套到新源码上。

工程提交 `d0c275e02e51739a2bd1ddc17b4ba6e3a4d05210` 的完整质量作业成功，但 Security 整体未通过。CodeQL 因 Web 构建文件散列变化正确拒绝旧绑定；[精确续审](../deployment/security/codeql-continuation-review-20261111.json)确认原四条技术结论及告警身份保持，未新增豁免。该次原始分析另报告两条合成验收探针的固定 IV 告警。探针现使用 `SecureRandom` 生成 AES 密钥和 12 字节 nonce，重新编译、真实隔离执行通过；旧扫描和旧执行均保留，新 CI 仍须实际确认这两条修复，不能把旧 SARIF 重判为没有告警。

同次 PostgreSQL 镜像检查报告 Kerberos 和 libxslt 合计 18 条 HIGH。只更新这两个库后的全级别本机扫描还报告 zlib 一条 MEDIUM，未以 HIGH 门槛通过代替全级别归零。[PostgreSQL 配方](../Dockerfile.postgres)现要求官方 `krb5-libs>=1.22.3-r0`、`libxslt>=1.1.43-r4`、`zlib>=1.4-r0`；本机使用核验签名的三个准确 APK 离线应用同样更新，保留 PostgreSQL 17.11、gosu 和完整运行配置。最终本机候选 `3018790e…` 的新库全级别原始发现及 secret 均为 0，失败中间镜像和报告保留。这是对已有制品的原生库修复，不能称为完整源码冷构建；完整配方的干净 CI、两个数据库角色的日常升级和数据验收另行核对。

[PostgreSQL 最终运行与路径证据](../deployment/security/postgres-native-20261111-evidence/configured-path-review.json)核对完整 3892 项文件、237 ELF，以及两个现有数据库实例的只读配置。三个签名包的实际 payload、APK 数据库和版本命令均与候选一致，PostgreSQL／gosu 及全部 `/usr/local` 文件字节保持。OpenSSL 库仍受上述五项公告影响；本次结论仅覆盖核实过的普通 TLS、GSS 配置、pgcrypto 固定算法白名单和当前入口，不称作 OpenSSL 固定版或任意管理员命令安全。

下文保留 2026-11-10 各阶段基线、原始扫描及失败范围；最终交付版本和镜像对应关系以[版本说明](container-packages.md)为准。

## 2026-11-10 的公开制品基线

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

CI 同时保留完整文件清单作为 JSON 诊断证据，不上传新第三方镜像二进制。普通版本的候选归档标签绑定源码 SHA 前 12 位，避免修复轮次覆盖此前本机候选；公开 Registry 仍只使用正式版本号，历史 artifact 规则保持。随后 `488550e` 的实际 CI／Security 全部成功，最终候选完成相应本地复核；11 月 11 日新漏洞库的增量与 Packages 阻断另见上方记录。不同阶段的失败和通过不合并为同一次验证。
