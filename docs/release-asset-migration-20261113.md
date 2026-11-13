# 辅助发布的兼容边界与下一版本准备

2026-11-13 只读核对：远端为 **9 个 Tag、9 个 Release、259 个附件、0 个草稿**。产品版本四个，共 206 个附件；五个辅助 Release 共 53 个附件，完整归档合计 3,503,526,912 字节。本次没有上传副本、改版本号、移动 Tag、删除 Release/附件或发布镜像。正式版本仍为 `v0.1.3`。

完整逐片目录由 [准备脚本](../scripts/prepare_release_asset_catalog.py)生成，见 [53 个原资产的身份与获取入口](../deployment/security/legacy-source-assets-catalog-20261113.json)。目录含原 Release ID、Tag、解析后的源码提交、asset ID、API/浏览器地址、逐片大小和 SHA-256、顺序及整包身份。它只证明所读元数据与原冻结运输契约一致；不是本次重新下载 3.5 GB、重新构建、运行或批准分发的证据。

| 保留入口 | Release ID | 附件 | 原消费者必须保留的条件 |
| --- | ---: | ---: | --- |
| `artifact-minio-fixed-20261026` | 424228449 | 1 | `fetch_approved_image.verify_release_publication` 核对原 Release ID/Tag；祖先来源检查还要求原 Git Tag 指向 `6bec3f00…`。 |
| `artifact-grafana-reviewed-20261026` | 424356978 | 13 | 同一发布身份检查及原 Git Tag/128 文件冻结证明；逐片获取继续绑定原 asset ID 和摘要。 |
| `artifact-grafana-exact28-20261028` | 426729499 | 13 | `fetch_grafana_candidate.verify_publication` 核对原 Release ID/Tag、发布时间及每个原资产在该 Release 中的身份。 |
| `artifact-grafana-maintenance-20261105` | 434994246 | 12 | 同一 loader 的独立 maintenance profile，继续绑定原运输文件和原 Release/资产。 |
| `artifact-grafana-fixed-20261107` | 437518277 | 14 | fixed20261107 loader 复用上述原发布身份及资产成员检查；当前 Go 源码构建仍使用该旧精确基底。 |

这五个入口目前不能清退。实际旧生产校验函数的默认网络路径离线反事实回放表明：只改同一个 Release 的 Tag，即使 asset ID/字节不变，也会被拒绝；将其替换成另一个 Release 对象、删除旧 Release 也会失败。回放只替换测试中的 HTTP 响应，不修改生产代码或远端，不称为真实迁移成功。原 128 文件祖先/当前提交/index/工作区逐字节证明另行实际通过。

旧 loader 另有显式 `--archive` 本地导入路径，仍校验精确整包和 OCI 身份，不声称远端发布通过；这可以作为未来单独验证的迁移入口。但它不能证明默认旧 CI 已兼容新远端。尤其 `build_grafana_source_20261110.reuse_exact_retained_input` 即使找到已预载的精确旧图像，也会核对原 live publication；两个原 Git Tag 的默认冻结检查也继续存在。当前没有把显式归档路径冒充为已经验证的完整替代构建。

GitHub 的 `/releases/assets/{asset_id}` 是原对象的获取路径；浏览器下载路径还含 Tag。原来将应用制品 Release 整体关联到 `v0.1.1` 能保留原 asset ID，并不意味着这些严格核对旧 Tag 的 loader 也能这样迁移。[GitHub Release API](https://docs.github.com/en/rest/releases/releases)、[资产 API](https://docs.github.com/en/rest/releases/assets)分别管理这些身份。

## 未来产品版本集中材料

新的应用交付只使用一个产品版本 Release，不新增诊断或运输 Tag。该版本可附上本目录，作为原来源的集中索引；已有历史必需材料继续从原固定入口获取。

若以后确有产品版本需要镜像这些旧材料，须先确定实际版本、原逐片与整包 SHA、对应的新 asset ID/URL、许可范围，再对新的匿名完整获取路径验证。随后只为新消费者建立明确的原身份→新身份映射。复制会产生新的资产对象，不能伪称原 asset ID 已搬家；镜像副本也不会使旧 loader 自动兼容。当前目录的目标 Release/new asset ID 保持空值，删除许可始终为 false。

因此，本次能保证的是未来材料集中、避免继续增设辅助发布，以及准确说明五个历史例外；不能为了让列表只剩版本而破坏未修改的历史获取契约。原正常 `v0.1.1`、`v0.1.2`、`v0.1.3` 材料继续保留，尤其 `v0.1.2` 的 74 个源码分片。

## 新应用版本的发布前提

是否发布下一版本，取决于本轮最终制品和真实验收结果；本记录不预先创建或批准 `v0.1.4`。

- 使用最终工程提交生成新的三应用 cohort、对应源码/许可入口和 publication manifest。Agent 的后续离线解析修复、Backend 的 Tomcat 11.0.27 不能由较早候选冒充；Web 只有在运行字节与配置完整相同时才可注明复用。旧失败模型流程、费用账本和旧候选均保留。
- 对最终实际组合完成已授权的真实模型及本地联网流程，确认业务结果与来源关系。必须另外核实各镜像及所用第三方服务的最终安全、来源和适用性；历史绿色 CI、较早单次成功、Trivy 为零或仅 HIGH 门槛通过不替代完整安全结论。
- 同一最终提交的最新 push CI、Security 必须成功；最终镜像、原生依赖、源码及法律材料的 hash、manifest/config/DiffID 与已验收候选一致。必要时重新构建受影响对象，不能改旧 pin、阈值或原始扫描凑通过。
- 产品 Tag 与 Release 必须解析到该最终提交。先准备可审查草稿、真实资产清单和完整字节校验，再由总控协调上传、正式发布与显式 Packages dispatch；本目录不执行这些远端动作。
- 公开发布后还需匿名获取指定 GHCR digest、核对身份/许可并运行验收。约定的本机日常更新独立进行，须保护原配置和数据，结束时恢复要求的状态。

[应用 Packages 工作流](../.github/workflows/publish-application-packages.yml)仅由 `workflow_dispatch` 触发，输入明确的产品 Release 和 manifest SHA。发布器检查同 SHA 的 push CI/Security、真实 Tag/Release、三应用归档和源码分片、加载后的配置身份及新扫描，再推送 GHCR；不因创建 Tag、Release 或普通 push 自动发布。独立 Grafana 发布入口同样为手动触发，不应为本次三应用交付顺带调用。

本次只读匿名 manifest 查询确认三个 `v0.1.3` 仍可见，摘要与已发布记录一致；三个 `v0.1.4` manifest 查询均返回 404。没有下载镜像层、登录 Registry、推送或启动容器；这个预检不代替最终版本的发布和匿名拉取验收。

现有本机旧 `v0.1.4` 候选和首次真实模型通过材料不能直接作为本轮最终交付；最终组合失败记录仍是历史事实。新的 Agent/Backend/source archive/manifest、真实组合收据、CI/Security 和匿名拉取结果必须绑定新的最终来源。

## Maven 读取权限

本次两个官方 Maven Packages 列表 GET 均实际返回 403，并说明当前凭据缺少 `read:packages`。因此 Maven 包库存与清退状态仍不能由 API 完整确认，不能把 403 或旧页面空列表写成“已归零”；本次没有提权或删除包。

当前工作流没有 `mvn deploy` 或 Maven 包发布入口，POM 仍正常用于编译、测试和镜像内 JAR。GHCR 工作流使用自己的仓库 `GITHUB_TOKEN` 与 `packages: write`，这是不同的权限路径；本机 PAT 的 Maven 列表失败不能推出 GHCR 发布一定失败或已获成功。[GitHub Packages 权限说明](https://docs.github.com/en/packages/learn-github-packages/about-permissions-for-github-packages)区分 Maven 的仓库权限与 Container Registry 的独立权限及公开匿名读取。

后续若要清退 Maven，应在有读取权限时核对准确包名/版本及已验证替代入口，再由总控按授权删除并复核；不以 Release/Tag 清退收据代替 Maven 清退结果。
