# Grafana 新候选审查与发布边界

状态：待完成第二份独立技术审查、接入代码复核和实际准备验证；尚无人类发布授权。本页不是上线批准。

| 身份 | 精确值 |
| --- | --- |
| 构建源码 | `c6acf98ca5d753b6981153f49108f43c62dbef0d` |
| 上游核心 | `Grafana 13.2.5 / ce471d869a4810cd83c987dd3521c4b67689dbb6` |
| 镜像索引 | `sha256:b774883fbc1f9878c8c4527d084821fd919408880613581ae5aa8a9aec63bfba` |
| OCI归档SHA256 | `83b436c99dce9568431c26bef58599f13aceca98e68f2fb7f0d1440e651f16c3` |
| 归档字节 | `916224512` |
| 拟发布tag | `artifact-grafana-fixed-20261107` |
| 拟选择镜像 | `deepresearch-approved-grafana:20261107` |

实际修复 Brotli 1.2.7、compress 1.20.3 和系统 zlib 1.4-r0，全部13个插件后端重建。隔离健康和13个插件settings返回200，加载器接受valid/private身份及精确新后端SHA；未连接13种外部数据源进行查询验收。四类实际篡改被加载器拒绝，原始日志及停机前快照指针均保留。

完整Trivy扫描40条（2 CRITICAL、11 HIGH、20 MEDIUM、2 LOW、5 UNKNOWN），完整Grype扫描10条（2 HIGH、3 MEDIUM、5 UNKNOWN、无CRITICAL、无ignoredMatches），完整Syft1758项。原始严重度、全部中低项及失败历史保留；这不是零风险声明。三条原生MEDIUM是busybox相关CVE-2025-60876，当前原始数据库未给修复版本，本次未授予中低项豁免。

以下18个精确元组属于阻断范围；Grype的7个阻断元组经精确别名对应其中项目。第一份独立复核结论为技术不适用：11条核心产品范围匹配已核实际13.2.5固定源码，2条Tempo对应未编入的服务端包并核官方修复后继，5条UNKNOWN对应未编入的旧OpenPGP包。每行依据、实际二进制SHA、完整编译图及原始审查记录均在精确request中绑定；第二份审查和最终工具准备仍待完成。

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

## 后续发布操作

审批对象将是上述精确归档与最终request SHA。预备操作包括：发布该归档的GitHub制品Release、生成并核验真实运输元数据、将Compose/CI验证选择器切至该精确制品，再执行对应检查。旧授权不能转授新subject。原始128项冻结材料和旧发布tag保留。

日常应用部署另设步骤，须具备有效恢复点并完成必要的索引迁移、模型质量和恢复验收。本轮不启动或替换日常容器，不重建历史向量，不扩大四次真实模型请求额度，不恢复已停用备份。
