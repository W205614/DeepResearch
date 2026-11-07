# Grafana 新候选审查与发布边界

状态：两份独立运行制品技术审查、接入代码复核及准备核验已完成。用户已明确批准下述精确归档和请求的发布及验证配置切换；14个分片于2026-11-07 11:37:42 UTC发布，真实运输元数据已独立核验。日常应用部署尚未执行，最终提交的CI与安全检查须独立核验。

| 身份 | 精确值 |
| --- | --- |
| 构建源码 | `c6acf98ca5d753b6981153f49108f43c62dbef0d` |
| 上游核心 | `Grafana 13.2.5 / ce471d869a4810cd83c987dd3521c4b67689dbb6` |
| 镜像索引 | `sha256:b774883fbc1f9878c8c4527d084821fd919408880613581ae5aa8a9aec63bfba` |
| OCI归档SHA256 | `83b436c99dce9568431c26bef58599f13aceca98e68f2fb7f0d1440e651f16c3` |
| 归档字节 | `916224512` |
| 已发布tag | `artifact-grafana-fixed-20261107` |
| 验证配置选择镜像 | `deepresearch-approved-grafana:20261107` |
| 请求SHA256 | `d9d2403921ed8f683aa4996a6c4a74dc892e1eac5290f6a3543afaf33a0e8d96` |

实际修复 Brotli 1.2.7、compress 1.20.3 和系统 zlib 1.4-r0，全部13个插件后端重建。隔离健康和13个插件settings返回200，加载器接受valid/private身份及精确新后端SHA；未连接13种外部数据源进行查询验收。四类实际篡改被加载器拒绝，原始日志及停机前快照指针均保留。

完整Trivy扫描40条（2 CRITICAL、11 HIGH、20 MEDIUM、2 LOW、5 UNKNOWN），完整Grype扫描10条（2 HIGH、3 MEDIUM、5 UNKNOWN、无CRITICAL、无ignoredMatches），完整Syft1758项。原始严重度、全部中低项及失败历史保留；这不是零风险声明。三条原生MEDIUM是busybox相关CVE-2025-60876，当前原始数据库未给修复版本，本次未授予中低项豁免。

Syft中28个本地源码模块的版本标记为大写`UNKNOWN`，已逐一绑定模块名、目标ELF、源码锁及文件哈希。新门禁对这28项核对完整Counter，其他未知、缺失、重复或身份错配版本均拒绝；真实完整报告正例及14项反例已独立验证。原始SBOM与此前审查记录保留。

以下18个精确元组属于阻断范围；Grype的7个阻断元组经精确别名对应其中项目。两份独立复核结论为技术不适用：11条核心产品范围匹配已核实际13.2.5固定源码，2条Tempo对应未编入的服务端包并核官方修复后继，5条UNKNOWN对应未编入的旧OpenPGP包。每行依据、实际二进制SHA、完整编译图及原始审查记录均在精确request中绑定；77目标的完整源码范围已核验，最终提交仍须自身的新鲜扫描回执。

| ID | 严重度 | 目标 | 包及扫描版本 | 技术结论 |
| --- | --- | --- | --- | --- |
| `CVE-2018-15727` | CRITICAL | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `CVE-2023-3128` | CRITICAL | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `CVE-2020-12458` | HIGH | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `CVE-2021-39226` | HIGH | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `CVE-2022-35957` | HIGH | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `CVE-2022-39307` | HIGH | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `CVE-2023-2801` | HIGH | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `CVE-2025-6023` | HIGH | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `CVE-2026-42127` | HIGH | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `CVE-2026-42129` | HIGH | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `CVE-2026-9029` | HIGH | `core` | `github.com/grafana/grafana@v0.0.0-20261027113329-ce471d869a48+dirty` | `NOT_AFFECTED` |
| `GO-2026-5932` | UNKNOWN | `core` | `golang.org/x/crypto@v0.58.0` | `NOT_AFFECTED` |
| `GO-2026-5932` | UNKNOWN | `elasticsearch` | `golang.org/x/crypto@v0.58.0` | `NOT_AFFECTED` |
| `GO-2026-5932` | UNKNOWN | `loki` | `golang.org/x/crypto@v0.58.0` | `NOT_AFFECTED` |
| `GO-2026-5932` | UNKNOWN | `mssql` | `golang.org/x/crypto@v0.58.0` | `NOT_AFFECTED` |
| `GO-2026-5932` | UNKNOWN | `stackdriver` | `golang.org/x/crypto@v0.58.0` | `NOT_AFFECTED` |
| `CVE-2026-21728` | HIGH | `tempo` | `github.com/grafana/tempo@v1.5.1-0.20260910130453-bcfe9f230c1d` | `NOT_AFFECTED` |
| `CVE-2026-28377` | HIGH | `tempo` | `github.com/grafana/tempo@v1.5.1-0.20260910130453-bcfe9f230c1d` | `NOT_AFFECTED` |

## 已批准的发布与验证切换

用户原文：“批准上述精确制品发布和验证配置切换”。[GitHub制品Release](https://github.com/W205614/DeepResearch/releases/tag/artifact-grafana-fixed-20261107)包含14个分片，共916224512字节；远端分片ID、大小、摘要及tag源码身份已独立核对。授权记录、真实运输及发布回执分别保存在`deployment/security/grafana-fixed-20261107-{authorization,transport,publication}.json`。Compose、两个CI工作流、运行清单生成器与README加载命令已按已审补丁切换。

原始request及[审批前准备回执](candidate-preparation.json)保留其历史未批准状态；新授权由独立文件记录，不改写旧授权或历史失败。[发布后回执](grafana-publication.json)记录本次实际结果。本地显式归档加载通过，切换后177项相关测试通过；远端下载、全量CI及新鲜安全扫描按最终提交核验。原始128项冻结材料和旧发布tag保留。

日常应用部署另设步骤，须具备有效恢复点并完成必要的索引迁移、模型质量和恢复验收。本轮不启动或替换日常容器，不重建历史向量，不扩大四次真实模型请求额度，不恢复已停用备份。
