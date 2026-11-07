# 镜像归档加载与安全门禁

MinIO 与 Grafana 分别绑定自己的请求、授权、归档、发布记录和标签。归档加载验证内容身份，Security 工作流验证完整扫描及逐项判定；镜像或绑定输入变化后重新审查。

```powershell
python scripts/fetch_approved_image.py --artifact minio
python scripts/fetch_grafana_fixed_20261107.py
# 离线加载使用对应命令的 --archive <归档路径>
```

## 当前 Grafana

Compose 与 CI 使用 `deepresearch-approved-grafana:20261107`，来自固定 Release `artifact-grafana-fixed-20261107`（ID `437518277`）。

| 材料 | 内容 |
| --- | --- |
| [精确请求](../deployment/security/grafana-fixed-20261107-request.json) | 实际源码、14 个 ELF、完整镜像、两份独立技术审查及逐项发现 |
| [授权](../deployment/security/grafana-fixed-20261107-authorization.json) | 本次明确批准的制品与18个精确条目 |
| [传输清单](../deployment/security/grafana-fixed-20261107-transport.json) | 14个分片、每片远端摘要、长度与上传状态 |
| [发布收据](../deployment/security/grafana-fixed-20261107-publication.json) | 真实发布、完整OCI、配置和层身份 |

归档为 `916224512` 字节，SHA256 为 `83b436c99dce9568431c26bef58599f13aceca98e68f2fb7f0d1440e651f16c3`。它由 `c6acf98` 的精确构建输入生成，后续应用提交加载同一归档并核对输入身份。

完整原始 Trivy 报告中的18条和 Grype 的7条阻断记录按目标、包、版本、公告与严重级别精确核对；两条 Tempo GHSA 仅映射到其对应 CVE。Syft 的28个 `UNKNOWN` 本地模块按源码、替换路径、目标与ELF逐项识别。13个插件后端均已重编，界面显示维护者私有信任；官方原始PGP签名继续验证其余资产。

截至 `b8153f5`，[Security 22个作业](https://github.com/W205614/DeepResearch/actions/runs/42370035403)全部成功，包括源码、原生运行时与当前Grafana门禁。原始报告中的其它级别和既有历史失败记录继续保留。

## 加载流程

加载器先验证请求、授权和绑定文件，再核对 OCI index → 平台 manifest → config → layers、解压后的 DiffID、whiteout／opaque 语义、目标二进制及祖先目录，最后检查 Docker 实际ID、平台和完整层集合。层内容以流式方式检查。MinIO归档上限512 MiB，Grafana独立上限2 GiB。

Grafana最多接受32个顺序分片，每片不超过64 MiB。每片校验长度与SHA后拼接，再核对整档；清单保留服务器返回的真实分片摘要。远端加载会核对固定Release的发布状态，本地 `--archive` 校验同一批准归档。

GitHub令牌只发送到固定仓库API；存储域名跳转重新建立不携带令牌的请求。错误日志隐藏签名URL与凭据。经典Docker存储遇到已识别的OCI导入兼容错误时，可生成仅补充运输元数据的临时tar，仍以原配置、层和ELF身份核验。

## 更新与历史材料

普通镜像保留既有HIGH／CRITICAL门槛；精确制品另按其绑定范围处理UNKNOWN、扫描失败、覆盖缺失和变化条目。工具与漏洞数据库按[原生运行时门禁](native-jvm-security-gate.md)维护锁定版本和时效。

2026-10-27起的旧发布、授权与128项冻结原料保留在原路径；旧Grafana标签和请求用于历史回放。原批准配方中的固定Python缓存同样按原字节保留，测试从实际源码在内存编译读取。当前入口与历史记录的对应关系见[生产化证据索引](evidence/production-20261107/README.md)。
