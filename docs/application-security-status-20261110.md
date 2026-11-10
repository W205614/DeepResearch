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

具体阻断包括 glibc 的旧库问题，以及仍需处理的 `libstdc++6` 发现。[CVE-2026-115456](https://security-tracker.debian.org/tracker/CVE-2026-115456) 涉及浮点 `std::from_chars` 读取非 NUL 终止输入，当前官方 GCC 14.4.0-4 仍未给出修复版本。镜像确有导出该函数的运行库；仅凭没有 C++ 头文件或部分 ELF 没有符号名，不能断言所有已编译调用均不受影响。未执行漏洞利用试验，不声称实际业务可利用，也不以此关闭告警。

后续发布现在必须重新扫描已经加载、且 portable config 身份匹配的实际归档，在推送前阻断全部严重级别与秘密发现。严格默认门槛也会拦截尚未接入精确复核的误报；它不把扫描器当作漏洞真值，更不允许凭清单中的 `security: true` 放行。原始结果和失败原因保留，没有改变既有源码或镜像扫描阈值以隐藏发现。

## 已落实的源码修复与验证边界

Agent 配方将 glibc 从 Debian 13 基础层的 2.41 更新为官方 `2.43-7`，同时安装严格匹配的 `libc-gconv-modules-extra`。保持 builder 工具链不变，在最终运行树提取完整官方文件，移除旧 gconv 树，并更新真实 control、md5sums 和版权。[来源锁](../deployment/security/agent-glibc-runtime-20261110.json)列出包及对应源码的 URL、长度和 SHA-256。

隔离候选已核对 290 个实际文件／软链的内容、模式和目标；CPython、NumPy、psycopg、httpx、Uvicorn、FastAPI 等导入及数值、压缩、内存 SQLite、TLS 信任库和两种 iconv 转换通过。这是兼容性探测，不是完整业务或日常升级验收。

本机完整源码构建两次因固定官方下载提前 EOF 失败，原摘要未改变。另用仓库实际 staging 脚本和相同官方包完成隔离构建、真实包元数据及上述原生探测；它不替代完整源码构建，后者仍须以相关 CI 的真实结果核对。

候选原始复扫仍有 83 条（3 HIGH、57 MEDIUM、23 LOW）：新增的 gconv 分包重复报告 22 个 glibc CVE，Debian 13 的数据也未自动识别 sid 固定版本。官方固定版本与实际文件身份必须逐项证明；不伪改 `os-release`、包记录或原始扫描来减少数字。其他库和争议项仍有待办，因此本轮没有据此新发版本或替换日常镜像。

原 `v0.1.1` Packages、Tag 与附件保持不可变，原 25 个日常容器保持停止。源码修复、候选兼容性、公开发布与日常环境验收分别记录，不能相互替代。后续须完成剩余精确复核、无官方修复项的实际解决、最终制品与业务验证后，才可按原 Registry／可见性发布新版本；不得覆盖现有版本。
