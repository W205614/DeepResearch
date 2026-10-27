# CI 付费解释器边界

2026-10-26 核对官方 `astral-sh/python-build-standalone` 最近六个发布，最新为 `20261021`，其 Linux x86_64 GNU 3.11 产物仍是 3.11.17，没有 3.11.18。不能仅给 setup-uv 写一个尚未发布的下载版本，也不能将生产镜像升级视为宿主解释器已经修复。

按已授权方案，Answer Quality 的 fresh 路径先用同一 checkout 的生产 `Dockerfile` 完整构建签名源码版 CPython 3.11.18。宿主执行 Git、GitHub 只读对账、必要预算预检、同 SHA CI/Security 门禁、开始标记和账本封存；原四个付费评测 target 在该镜像内执行。四阶段 case、flags、断言、阈值及累计 515 次新增请求授权不变。job 上限为 90 分钟，同 SHA 前置门禁等待仍为 900 秒，未成功时明确零付费失败。

付费开始前，镜像必须在 `--network none` 下证明真实版本为 3.11.18、上游受损 CA 固定 fixture 握手返回验证码 28、临时合法 CA 的 MemoryBIO TLS 握手成功。上游 ZIP 与来源 tar SHA 固定，公开测试私钥不是应用密钥。该负例强制信任测试根后验证 CPython 专项 SPKI 拒绝，不证明 Java 有相同漏洞或默认信任该根。

宿主随后验证 clean HEAD 等于已对账的 run SHA，计算全部 Git tracked 文件字节摘要，并核对镜像内生产源码清单。独立来源凭证包含 run/attempt/SHA、实际镜像 ID、UID/GID 和预检摘要；其摘要持久写入 SQLite 的当前及不可替换的 attempt 记录。容器重新校验来源文件、账本摘要、真实解释器和进程身份后，才能安装供应商 HTTP 计费 hook。研究报告的提交信息取自该凭证，无需在 distroless 中运行 Git。旧账本仍可用于累计消耗对账，不因此取得质量结论复用资格。

容器使用宿主的非 root UID/GID、只读源码和根文件系统，仅 `.cache/eval` 为可写绑定目录，另有 128 MiB 临时文件系统，限制 2 CPU、2 GiB 内存及 256 PID，移除全部 capabilities，启用 no-new-privileges。来源凭证与预检文件在可写父目录之上单独只读挂载。只按环境变量名称传递四个模型配置变量，不展开密钥，不传 GITHUB_TOKEN。checkout 不持久保存 GitHub 凭据。

实际零网络容器预检通过：Python 3.11.18、CA code28、合法 TLS1.3、UID/GID10001，88 个镜像源码文件与当前工作区一致，供应商及网络 HTTP 均为 0。原完整生产构建、解释器签名/ABI 和完整镜像扫描证据见 `full-python-build.json`，本项没有重建或改变运行服务。

针对性验证共覆盖 50 个独立用例。首次四个相关测试文件为 48 通过、1 个旧的 45 分钟静态断言失败；按授权更新为 90 分钟后，仅重跑该断言及 17 个新边界用例，18 项通过；另加报告从凭证取 SHA、禁止 Git 的用例，1 项通过。受影响脚本与测试的 Ruff 检查通过。包含 dirty/错 SHA、源码/预检/凭证/账本摘要篡改、错误 attempt、受损 CA 非预期错误、旧版本/root/联网预检、未验证宿主安装计费 hook、容器参数和原评测参数保持等反例。未执行供应商请求、未 push 或触发 CI。

剩余验收：最终提交对应的 GitHub 正式 recipe 构建、同 SHA CI/Security、真实历史 artifact 对账及 fresh 四阶段质量结果。本地 mock 账本验证和生产镜像零网络预检不是上述远端验收结果，515 次也不保证质量通过。

官方来源：[standalone releases](https://github.com/astral-sh/python-build-standalone/releases/tag/20261021)、[CPython 3.11.18](https://www.python.org/downloads/release/python-31118/)。原始 API、禁网收据及源码摘要位于 `.cache/optimization-20261026/ci-python/`，具体 SHA 见同目录公开 JSON 证据。
