# Grafana 2026-11-07 未批准候选输入

此目录只准备安全修复候选，不改变活动运行选择器，不继承旧镜像的人类授权，不发布或部署制品。

- 上游 core 固定为 Grafana `v13.2.5` / `ce471d869a4810cd83c987dd3521c4b67689dbb6`。
- 13 个现有插件保持相同官方前端/资源版本，全部重编后端。每个源模块都有固定 Git commit、官方 Go 模块 h1 / go.mod h1；Prometheus 的 `pkg/promlib` 从相同 Git 提交的真实子树取得。
- 依赖修复为 brotli `v1.2.7`、compress `v1.20.3`，并将五个此前官方二进制对应源模块的 x/net 同步到既有安全基线 `v0.62.0`。Go 解析器同步了该版本要求的 x/crypto、x/sys、x/term、x/text 最小版本。
- 现有官方插件完整资产来自 `source.lock.json` 指定的精确旧镜像，只复用字节与来源；不会继承其漏洞处置或发布批准。

使用仓库现有严格授权制品获取流程先加载 `deepresearch-approved-grafana:20261105`。构建入口会核对其实际本地 ID 为 `sha256:5a14435f088e735cbaba6e6375c5ea03e34424ea3934b55e098a2fb660db452a`。

在提交本目录和独立 Dockerfile 后，从仓库根目录执行：

```powershell
.venv/Scripts/python.exe deployment/vendors/grafana-20261107/build_candidate.py --output-dir .cache/grafana-20261107/final
```

入口默认拒绝未提交的候选输入，记录 DeepResearch 项目提交、输入文件实际字节 SHA256、工作区状态和独立的上游 Grafana 提交。最终包装逐文件核对捕获提交的 Git blob；只有 Git 判定为文本、clean 对象恰等于捕获 blob、且差异仅为 CRLF→LF 时才允许换行规范化，并记录原始/提交 SHA256 与 Git 属性，不声称原字节相同。`-text`、二进制、过滤器或其他内容差异均拒绝。`--allow-uncommitted-inputs` 只用于本地编译诊断，不能作为最终精确授权申请的源码身份。

预取只改变下载传输：从 `proxy.golang.org` 获取相同官方版本，随后 Go 继续使用默认 `sum.golang.org` 校验，构建器继续核验 h1 与实际 Git revision。因本轮已经观察到网络 EOF，单个预取文件最多尝试三次，失败会中止。没有关闭校验或改用任意模块镜像。公共文件持久缓存在 `.cache/grafana-20261107/module-proxy`；新输出目录复用已完整下载的相同版本文件，避免因大 ZIP 传输失败反复下载。每次运行重新记录实际缓存文件 SHA256 与尝试次数，并将本轮传输 receipt 复制到独立输出目录；旧失败输出保留。复用后 Go 的默认 SumDB、源模块 h1、实际依赖和 readonly 校验仍照常执行。

原有 PGP 验证继续覆盖原始官方资产。新 ELF 通过编译到 core 的精确原始 manifest/原始后端/新后端 SHA256 映射校验，身份明确为 `private` / `DeepResearch maintainer rebuild`，不是官方签名的新二进制。生产图使用与实际后端相同的 `arrow_json_stdlib` 标签；core 保持原构建标签。最终镜像还会校验 14 个实际 ELF 中的修复依赖版本。

构建输出、完整原始扫描、实际 ELF/生产依赖图、隔离启动和篡改检查，以及独立精确技术审查必须绑定同一最终镜像与 archive。所有剩余 finding 均保留原始严重度；本目录不提供豁免、风险接受、人类批准或门禁放宽。发布、运行选择器修改和日常上线需要后续精确授权。
