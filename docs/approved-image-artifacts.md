# 精确镜像归档的加载与门禁

MinIO 与 Grafana 各自有独立的审批策略、镜像标签、归档散列、证据文件和到期时间。`scripts/oci_artifact.py` 只验证事实，不授予漏洞判定或部署许可。Compose 和 CI 加载获批的同一归档；重新构建出来的镜像必须重新审查。

```sh
python scripts/fetch_approved_image.py --artifact minio
python scripts/fetch_grafana_candidate.py
```

加载器先验证策略及绑定文件，再流式验证完整 OCI index → platform manifest → config → layers、解压后的 DiffID、whiteout/opaque 后的目标文件及其祖先目录，最后核对 Docker 实际 ID、平台和完整 diffIDs。不会把层解压到宿主文件系统。MinIO 保持 512 MiB 归档上限；Grafana 独立上限为 2 GiB。不识别的压缩格式或不安全层语义会拒绝加载。

Grafana 的 `github_asset_parts` 传输只接受固定仓库、release 与顺序分段名，最多 32 段，每段最多 64 MiB。当前归档有 13 段；清单必须齐全，每段都有服务器返回的 `remote_digest` 和 `uploaded` 状态。每段下载后校验长度与 SHA，按顺序流式拼接后再校验整档长度与 SHA；没有整档服务器 digest，不会伪造这一字段。经验证的分段可以复用，损坏或未完成文件保留以便调查，不会提升为有效归档。

GitHub token 仅发往固定仓库的 API URL。自动重定向关闭；仅允许批准的 HTTPS GitHub 存储域名，存储请求重新创建且不携带 token，第二次重定向失败关闭。错误输出不打印签名下载 URL 或凭据。

加载成功只证明归档和镜像身份一致。Security CI 仍需生成完整原始报告、满足包覆盖与扫描器成功条件，再运行对应独立判定器。普通镜像继续使用原 HIGH/CRITICAL 失败门禁。Grafana 当前使用[新精确请求](../deployment/security/grafana-candidate-request.json)与[明确授权](../deployment/security/grafana-candidate-authorization.json)：28 条逐目标技术不适用判定由完整生产依赖图、实际 ELF 与真实应用版本支持；Trivy 的 28 条和 Grype 的 7 条阻断记录分别精确核对，两条 GHSA 仅作指定别名映射。28 个缺少 Version 的本地模块另按实际源码、替换路径和 ELF 身份识别，不编造版本。中低危原样保留。

当前 Grafana 的[已发布传输清单](../deployment/security/grafana-candidate-transport.json)包含13段，每段都有实际远端 SHA 与 uploaded 状态；完整归档为859353088字节，SHA256为 `849d3d86c9d51c6bda4c78d3091f7d457ba93dbd37e66aefcc87ef57b25cda70`。[发布收据](../deployment/security/grafana-candidate-publication.json)绑定真实构建提交、完整 OCI、32 个 DiffID 与14个 ELF。归档来自固定 Release `artifact-grafana-exact28-20261028`（ID426729499），不依赖短期 Actions 工件。

Ubuntu24等经典 Docker 存储遇到明确的 `docker-import-…/blobs/json` 缺失错误时，加载器可生成私有临时运输 tar，仅增加引用原 config/层 blob 的 Docker `manifest.json`；原归档与全部成员内容保持不变，派生 SHA 只作运输校验。加载后仍核对原批准配置与全部 DiffID。[实际兼容验证](../deployment/security/grafana-candidate-classic-load-evidence.json)逐一核对了14个加载后 ELF，临时运输目录已回收。其他错误不会触发这一回退。

2026-10-27的[旧发布来源收据](evidence/optimization-20261026/published-artifact-origin.json)与128项冻结原料保留为历史证据；其中的旧 Grafana 授权不被新镜像继承。MinIO 继续使用自己的原固定发布。默认远程入口每次实时验证当前固定发布状态，缓存不能绕过；本地 `--archive` 只验证同一获批归档，不提供远程发布证明。新 Grafana 实际构建于 `ee370ae`，后续提交加载同一归档并核对全部当前输入，不称作重新构建。最终结果以对应应用提交的 CI/Security 为准。缺失、过期、新增或变化的阻断记录、扫描失败及绑定漂移均阻断。

原批准配方包含一份固定 Python 缓存，已按原始字节随配方交付。测试从实际 `.py` 在内存编译读取，不依赖或改写该缓存；不自动纳入其他被忽略文件。128项冻结文件的 Git 索引核对及本次只读加载回归见[交付记录](evidence/optimization-20261026/frozen-recipe-git-delivery.json)。后续清理这份原输入仍须重新冻结审查，不能静默改变本次批准范围。
