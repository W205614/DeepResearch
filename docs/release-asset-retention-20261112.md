# 2026-11-12 版本页合并与辅助发布保留依据

本次整理以 `v0.1.3` 为当前产品版本，不新增版本、不移动产品 Tag、不重推或覆盖现有镜像。产品版本保留 `v0.1.0`、`v0.1.1`、`v0.1.2`、`v0.1.3` 各自的源码边界。下列判断来自实际 GitHub Release/asset 清单和当前仓库调用关系，不能据名称或年代直接删除材料。

后续 main 候选仍未创建 `v0.1.4` Tag／Release、推送候选公开镜像或更新日常环境。[候选验证目录](evidence/release-v014-20261112/README.md)记录第一轮 `607626a2…` 真实研究完成（累计 32 次请求，上界预留 3.547734 USD），以及最终组合 `8e83be7f…` 再次 `insufficient`（累计 40 次、4.534515 USD）。剩余额度不足完整复验，后续仅离线修正资料归因和引用位置解析，未新增真实模型调用。MinIO／Grafana 的 16 条新增 stdlib 记录已通过 Go 1.27.4 真实源码重建消除，其他原始记录保留；安全门槛通过不替代最终流程验收，也不产生删除旧来源或发布新版本的依据。

## 已迁入产品版本页的应用制品

原 `artifact-applications-v0.1.1-r1` 的完整 88 个附件已关联到现有 [v0.1.1](https://github.com/W205614/DeepResearch/releases/tag/v0.1.1)：

- 源码 Tag 保持 `b2da9da7c390bd50944c2947afd1381613c37192`；镜像分发来源仍为 `0005df53b742cdc2b79ef9196208cf0d00f8a0b8`，原运行字节仍绑定 `b8d1500`。
- 原制品 Release ID `439279902`、全部 asset ID、文件名、长度、SHA-256 和字节保持，合并源码说明；移除原无独有附件的源码说明 Release ID `439055514`，产品 Tag 未删除或移动。
- 浏览器下载路径改为 `/releases/download/v0.1.1/文件名`，GitHub REST `/releases/assets/{asset_id}` 路径保持。原始 `publication-manifest.json` 不改写，其 `artifact_release` 字段是历史出处，不是新下载地址或重新发布指令。
- 已核对全部 88 个原资产的身份元组，并通过新版本路径匿名重新下载原清单、验证完整 SHA-256。原资产身份未变，继续复用此前完整材料和镜像验收；不声称本次重新构建或重新做了旧镜像的运行验收。
- 初次尝试复制附件时发生过 HTTP 200 正文提前结束。已验证的复制件和原件身份相同；最终改用原 Release 重新关联，保留原对象，复制尝试及其失败记录只留本机。

README 和下载文档已在提交 `e8b1e372a5b90d83ed282d6f5de4bc30ac2f8006` 推送到远端 main；四份文件的完整远端字节均与本地相同。随后实际删除旧辅助 Tag `artifact-applications-v0.1.1-r1`，官方 API 复核为 404。原制品 Release 已并入 `v0.1.1`，不再占用单独的辅助发布入口。

清退后实际远端为 **9 个 Tag、9 个 Release：4 个产品版本及下述 5 个有依赖的第三方保留例外**，没有草稿。4 个产品版本的 Tag/源码提交保持原值，`v0.1.1` 保留完整 88 附件；`v0.1.2` 保留 93 附件，`v0.1.3` 保留原 24 附件并另加 2026-11-12 的 CI 补充收据（现 25 项）。没有删除容器包或修改镜像摘要。

## 保留的五个第三方发布

| Tag / Release | Release ID | 具体引用及保留原因 |
| --- | ---: | --- |
| [artifact-minio-fixed-20261026](https://github.com/W205614/DeepResearch/releases/tag/artifact-minio-fixed-20261026) | 424228449 | 正式 `v0.1.3` 及历史源码的固定 MinIO/Silo 下载，仍是 main 源码重建的保留基底。`scripts/fetch_approved_image.py` 绑定 Release ID、Tag、asset ID `678114320` 和归档摘要；`scripts/check_artifact_source_reuse.py` 仍核对祖先提交。main 默认运行入口已改为 `build_go_stdlib_security_20261112.py --artifact minio`，不表示旧固定材料可以删除。 |
| [artifact-grafana-reviewed-20261026](https://github.com/W205614/DeepResearch/releases/tag/artifact-grafana-reviewed-20261026) | 424356978 | 原 Grafana 的分片运输和来源祖先绑定。`scripts/fetch_approved_image.py` 与 `scripts/check_artifact_source_reuse.py` 使用固定 Release/Tag；原始层、源码及法律材料仍有来源职责。不能用较新但字节不同的 Grafana 替换。 |
| [artifact-grafana-exact28-20261028](https://github.com/W205614/DeepResearch/releases/tag/artifact-grafana-exact28-20261028) | 426729499 | `scripts/fetch_grafana_candidate.py` 的默认 exact28 消费路径；`deployment/security/grafana-candidate-transport.json` 固定 13 个分片 ID，运输文件本身受 SHA-256 绑定。保留原独立制品和验证路径。 |
| [artifact-grafana-maintenance-20261105](https://github.com/W205614/DeepResearch/releases/tag/artifact-grafana-maintenance-20261105) | 434994246 | `scripts/check_grafana_maintenance.py`、`scripts/check_grafana_active_source_retirement.py`、`scripts/grafana_source_candidate4.py` 仍核对维护制品、运输摘要和来源退役证据；12 个分片不是普通临时日志。 |
| [artifact-grafana-fixed-20261107](https://github.com/W205614/DeepResearch/releases/tag/artifact-grafana-fixed-20261107) | 437518277 | 正式 `v0.1.3` 的 `scripts/build_grafana_source_20261110.py` 会调用固定 loader；main 的 `build_go_stdlib_security_20261112.py --artifact grafana` 继续复用旧精确输入。`scripts/check_grafana_release_20261107.py` 同时绑定实际来源、运输和发布材料；14 个分片仍承担构建输入职责，不能因当前 Go 重建而删除。 |

这些是有具体依赖的保留例外，不称作“Tag/Release 已全部只有产品版本”。后续合并需要迁移准确字节及固定下载绑定，并验证当前和仍支持的来源路径；单纯更名或删除 Tag 会破坏现有契约。本次不更改第三方安全批准、摘要或冻结的历史配方，也不新增其公开二进制分发。

正常历史 `v0.1.2` 的 93 个附件继续保留，其中 74 个源码分片仍被当前 `v0.1.3` 的对应源码清单引用，不在辅助发布删除范围内。

## 可复核入口

GitHub REST `GET /repos/W205614/DeepResearch/releases?per_page=100` 与 `GET /repos/W205614/DeepResearch/git/matching-refs/tags/` 可分别检查实际 Release 和 Tag；带注释的 Tag 还须解析到 commit。`python scripts/check_artifact_source_reuse.py --output .cache/artifact-source-reuse.json` 核对冻结第三方来源的原 Tag、祖先关系和 128 个文件身份，不发出模型请求、不更新日常环境。
