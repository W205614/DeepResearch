# 精确镜像归档的加载与门禁

MinIO 与 Grafana 各自有独立的审批策略、镜像标签、归档散列、证据文件和到期时间。`scripts/oci_artifact.py` 只验证事实，不授予漏洞判定或部署许可。Compose 和 CI 加载获批的同一归档；重新构建出来的镜像必须重新审查。

```sh
python scripts/fetch_approved_image.py --artifact minio
python scripts/fetch_approved_image.py --artifact grafana
```

加载器先验证策略及绑定文件，再流式验证完整 OCI index → platform manifest → config → layers、解压后的 DiffID、whiteout/opaque 后的目标文件及其祖先目录，最后核对 Docker 实际 ID、平台和完整 diffIDs。不会把层解压到宿主文件系统。MinIO 保持 512 MiB 归档上限；Grafana 独立上限为 2 GiB。不识别的压缩格式或不安全层语义会拒绝加载。

Grafana 的 `github_asset_parts` 传输只接受固定仓库、release 与顺序分段名，最多 32 段，每段最多 64 MiB。当前归档有 13 段；清单必须齐全，每段都有服务器返回的 `remote_digest` 和 `uploaded` 状态。每段下载后校验长度与 SHA，按顺序流式拼接后再校验整档长度与 SHA；没有整档服务器 digest，不会伪造这一字段。经验证的分段可以复用，损坏或未完成文件保留以便调查，不会提升为有效归档。

GitHub token 仅发往固定仓库的 API URL。自动重定向关闭；仅允许批准的 HTTPS GitHub 存储域名，存储请求重新创建且不携带 token，第二次重定向失败关闭。错误输出不打印签名下载 URL 或凭据。

加载成功只证明归档和镜像身份一致。Security CI 仍需生成完整原始报告、满足包覆盖与扫描器成功条件，再运行对应独立判定器。普通镜像继续使用原 HIGH/CRITICAL 失败门禁。Grafana 当前授权是 2 项 FIXED、16 项 NOT_AFFECTED；两个 Grype GHSA 别名继承对应 FIXED，不能将所有条目统一标成 NOT_AFFECTED。28 个缺少 Version 的本地 workspace module 仅按精确目标、源码 directive、源码 hash 和 ELF 身份识别，与 18 项漏洞判定分开；不造版本、不忽略新发现。中低危原样保留。

当前 Grafana 的[实际远程传输收据](evidence/optimization-20261026/grafana-remote-transport.json) 已验证全部13段及各段 SHA，849540608 字节全部来自远程下载，按顺序重组后的整档 SHA256 为 `02332c39f6daed922cd030fbcaa00e4949e07b1c157776a2ca60640327e4e59b`，完整 OCI 校验通过。原始短流失败和分段重试材料保留，没有使用本地源归档补齐，也没有伪造整档服务器 digest。

[发布来源收据](evidence/optimization-20261026/published-artifact-origin.json)记录两份固定release已于2026-10-27发布，并在官方API回读中保持prerelease/非draft状态；两个原tag仍指向6bec3f00d883f0de0aac89b29d3a30cd7325fdf3。默认远程入口每次仍实时验证发布状态，缓存不能绕过。本地 `--archive` 只验证同一获批归档，不提供远程发布证明。新的应用提交通过 `scripts/check_artifact_source_reuse.py` 验证祖先关系、原tag和全部128文件在原提交/新提交/index/工作区的原始字节，再独立完成归档/ELF/新扫描门禁；旧制品不被称为新提交重建。此前草稿审计、当前发布元数据和最终应用SHA的CI分别记录，最终CI仍待完成。任何缺失、过期、未知、扫描失败、新发现或绑定漂移均阻断；本说明不授予日常部署或清理许可。

原批准配方包含一份固定 Python 缓存，已按原始字节随配方交付。测试从实际 `.py` 在内存编译读取，不依赖或改写该缓存；不自动纳入其他被忽略文件。128项冻结文件的 Git 索引核对及本次只读加载回归见[交付记录](evidence/optimization-20261026/frozen-recipe-git-delivery.json)。后续清理这份原输入仍须重新冻结审查，不能静默改变本次批准范围。
