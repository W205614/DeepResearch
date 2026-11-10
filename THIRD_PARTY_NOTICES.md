# 第三方代码、素材与分发说明

核查日期：2026-11-07。根目录 [MIT LICENSE](LICENSE) 适用于 W205614 有权授权的独立原创代码和文档。第三方源码、字体、依赖、容器基础层及其派生程序保留各自的版权与许可证；MIT 不替换这些许可证，也不授权第三方商标。

本文列出已识别的来源和发布前仍需验证的事项，**不代表所有二进制分发义务已经完成**。固定版本、源码提交和字节身份以对应锁文件、构建配方与实际制品记录为准；历史锁和原始证据保持原样。

应用源码版本仅提供 Git 跟踪的项目源码和既有第三方声明，不附预构建容器、依赖包或运行数据；具体流程见[源码发布与运行](docs/source-release.md)。源码内的第三方文件仍按本页所列各自许可。既有 `artifact-*` 镜像及下载器仍单独管理，其待验事项不会因应用源码发版而消失；CI 临时镜像归档也不能仅因保留时间短就视为不涉及分发。

## 仓库内保留的第三方内容

| 内容与位置 | 许可与已保留说明 | 来源与边界 |
| --- | --- | --- |
| Bricolage Grotesque 字体，`frontend/public/fonts/` | SIL Open Font License 1.1；[原版权与全文](frontend/public/fonts/OFL.txt) | Copyright 2022 The Bricolage Grotesque Project Authors；[字体来源](frontend/public/fonts/README.txt)。字体不改为 MIT。 |
| Docker Library Python 原始配方，`deployment/vendors/python311/Dockerfile.upstream` | MIT；[原版权与全文](deployment/vendors/python311/LICENSE.docker-library-python) | Copyright (c) 2014 Docker, Inc.；[精确上游提交与配方摘要](deployment/vendors/python311/source-lock.json)。CPython 本身与运行时依赖另按其许可证。 |
| Loki `pkg/push` 的 Go／Proto 文件，`deployment/vendors/loki/push/` | Apache-2.0；[原许可证](deployment/vendors/loki/push/LICENSE) | [固定上游提交、原文件摘要及模块锁修改](deployment/vendors/loki/source.lock.json)。此子目录的许可不能代表整个 Loki 服务。 |
| OpenTelemetry Collector 生成源，`deployment/vendors/otel-collector/source/` | Apache-2.0；[许可证](deployment/vendors/otel-collector/LICENSE) | [生成器、组件选择与构建说明](deployment/vendors/otel-collector/README.md)、[来源锁](deployment/vendors/otel-collector/source.lock.json)。保留生成文件标记。 |
| Milvus／milvus-storage／Arrow 头文件与相关构建输入，`deployment/vendors/milvus/` | Apache-2.0；[Milvus 许可](deployment/vendors/milvus/licenses/milvus-LICENSE)、[storage 许可](deployment/vendors/milvus/licenses/storage-LICENSE) | [每项上游提交、归档与头文件来源](deployment/vendors/milvus/source.lock.json)。归档内文件声明继续适用。 |
| Milvus telemetry 示例原文测试夹具，`tests/fixtures/milvus-telemetry-example-20261110/` | Apache-2.0；[Milvus 许可](deployment/vendors/milvus/licenses/milvus-LICENSE)，原文件头保留 | 来自上述固定 Milvus 源码归档，仅验证维护补丁可应用；补丁及修改范围见夹具来源说明。 |
| librdkafka `rdkafka_mock.h`，`deployment/vendors/milvus/` | BSD 两条款；版权、条件和免责全文保留在[文件头](deployment/vendors/milvus/rdkafka_mock.h) | Magnus Edenhill（2019–2022）及 Confluent Inc.（2023）；其他头文件按上述来源锁及各自文件声明。 |
| Debian／IANA 时区数据，`deployment/vendors/etcd/runtime-data/` | 原包版权文件；IANA 数据为 public domain | [原版权文件](deployment/vendors/etcd/runtime-data/tzdata.copyright)、[legacy 版权文件](deployment/vendors/etcd/runtime-data/tzdata-legacy.copyright)、[来源记录](deployment/vendors/etcd/runtime-data/provenance.json)。Debian 包中的其他文件保留原许可。 |
| 其他运行时 CA／时区包 | 按原软件包版权，不统一归为 MIT | [包来源与摘要](deployment/vendors/runtime-data/packages.lock.json)、[打包说明](deployment/vendors/runtime-data/README.md)。 |

## AGPL 组件与对应源码

Grafana、Loki、Tempo 和 MinIO／Silo 是独立部署的第三方程序。其上游代码、修改后的派生程序及适用的补丁保留对应 AGPL 条款；根 MIT 不改变该范围。仅因容器或仓库一起交付，不能省略逐组件的分发义务，也不能据此把所有原创应用代码笼统标为 AGPL 或把第三方程序标为 MIT。

| 组件 | 精确源码入口与本地修改材料 |
| --- | --- |
| 当前 Grafana 13.2.5 | [上游固定提交](https://github.com/grafana/grafana/tree/ce471d869a4810cd83c987dd3521c4b67689dbb6)、[该版本许可范围](https://github.com/grafana/grafana/blob/ce471d869a4810cd83c987dd3521c4b67689dbb6/LICENSING.md)。默认 AGPL-3.0-only，部分目录另有许可。当前批准制品的 DeepResearch 构建输入为 [`c6acf98ca5d753b6981153f49108f43c62dbef0d`](https://github.com/W205614/DeepResearch/tree/c6acf98ca5d753b6981153f49108f43c62dbef0d)，包括 `deployment/docker/Dockerfile.grafana-20261107`、`deployment/vendors/grafana-20261107/` 与复用的 `deployment/vendors/grafana/` 输入。 |
| 13 个 Grafana 插件 | [逐插件源模块、版本、提交和摘要](deployment/vendors/grafana-20261107/plugin-modules.json)；依赖锁及签名验证修改由上述固定构建输入提供。每个插件及其依赖按自身许可，不能仅凭 Grafana 主程序许可推定。官方前端资产继续来自锁定的原始插件，修改后的后端明确使用维护者信任。 |
| Loki 3.7.9 | [上游固定提交](https://github.com/grafana/loki/tree/d3b4a2f76bfc5625abee7ceb00b8896fa3c31549)、[该提交许可证](https://github.com/grafana/loki/blob/d3b4a2f76bfc5625abee7ceb00b8896fa3c31549/LICENSE)、[本地来源及修改锁](deployment/vendors/loki/source.lock.json)、[构建配方](deployment/docker/Dockerfile.loki)。 |
| Tempo 2.10.10 | [上游固定提交](https://github.com/grafana/tempo/tree/f317720e7e5191a7f282e8f7d6fe5387ae6015b4)、[仓库内 AGPL 全文](deployment/vendors/tempo/LICENSE)、[本地来源及修改锁](deployment/vendors/tempo/source.lock.json)、[构建配方](deployment/docker/Dockerfile.tempo)。 |
| MinIO／Silo | [Silo 固定提交](https://github.com/pgsty/silo/tree/0cd388c666f8211f552604ae73448a066e9c25ce)、[该提交 AGPL 许可](https://github.com/pgsty/silo/blob/0cd388c666f8211f552604ae73448a066e9c25ce/LICENSE)、[本地原许可副本](deployment/security/minio-fixed-evidence/UPSTREAM-LICENSE.txt)、[构建配方](Dockerfile.minio)、[兼容性补丁和测试](deployment/vendors/minio/)、[批准制品绑定](deployment/security/minio-fixed-disposition.json)。镜像还包含 `mcli`，其源码和依赖也须纳入对应核查。 |

修改记录和日期见固定提交历史、来源锁、补丁、生成器及[当前批准制品说明](docs/approved-image-artifacts.md)。这些入口提供可核对的来源，尚不能单独证明某个发布附件已经提供完整的 Corresponding Source。

[AGPLv3 第 1、5、6、13 节](https://www.gnu.org/licenses/agpl-3.0.en.html)规定对应源码、修改声明、非源码分发和修改版网络服务的要求。网络下载二进制采用第 6(d) 节方式时，需要在二进制旁提供明确的对应源码获取指引；源码可位于另一服务器，但分发者仍负责其可用性。修改版网络服务还须向远程交互用户显著提供源码获取机会。源码应覆盖实际版本、修改、构建／安装脚本及适用的依赖，不能只给上游首页或另一版本。另见 [GNU FAQ 的对应源码说明](https://www.gnu.org/licenses/gpl-faq.en.html#AnonFTPAndSendSources)和 [Grafana 官方许可说明](https://grafana.com/licensing/)。

截至本次核查，既有 Grafana／MinIO 制品 Release 主要提供 OCI 归档或分片；本轮未完成其完整对应源码包、插件各自许可、下载路径及网络服务源码入口的逐项验收。再次正式分发这些二进制前，需要：

1. 对照实际 ELF、依赖图、源码提交和实际模块锁整理完整对应源码；保留原 LICENSE、版权、NOTICE 和修改日期，核对每个插件与依赖的分发条件。
2. 验证源码可以免费获取且与目标二进制一致，运行构建入口验证所需材料；在二进制 Release 旁给出准确指引。可使用同一 Release 的源码附件，或满足第 6(d) 节条件的等效网络路径。
3. 核对修改版服务向远程用户提供的显著源码入口。修改精确镜像需要重新构建、扫描和审查；不得把新增说明当作旧制品已自动完成合规。

## 应用依赖与容器运行时

Python 依赖由 [pyproject.toml](pyproject.toml) 和 [uv.lock](uv.lock) 锁定；Java 依赖由 [Maven 配置](business-backend/pom.xml)解析；前端依赖由 [package-lock.json](frontend/package-lock.json)锁定。它们属于独立第三方软件，各包附带的版权、许可和 NOTICE 继续适用。

本次本地元数据检查识别到：

- Python 栈包含 MIT、Apache-2.0、BSD、Pillow 的 MIT-CMU、`psycopg`／`psycopg-binary` 的 LGPL-3.0-only，以及 `pypdfium2` 的 `(Apache-2.0 OR BSD-3-Clause) AND LicenseRef-PdfiumThirdParty`。PDFium 的第三方清单、Psycopg 和其二进制捆绑库需要随实际分发核对，不能概括为全 MIT。
- 前端锁包含 MIT、Apache-2.0、ISC、BSD 和 MPL-2.0。DOMPurify 为 `MPL-2.0 OR Apache-2.0`，Lucide 为 ISC；开发工具 Lightning CSS 的 MPL 许可适用于其自身。打包前端时保留适用版权与许可；开发依赖是否进入附件或镜像要按实际产物判断。
- Java／Python 依赖的完整传递闭包、原生库、容器基础层和第三方程序各有分发要求；安全扫描成功不等同于许可证验收通过。Keycloak、PostgreSQL、Redis、Milvus、ClamAV、Prometheus、Alertmanager、OpenTelemetry Collector 及其运行时按各自固定版本的上游许可分发。

本次未对每个传递依赖和基础层文件出具完整的许可证清单，也未把构建锁当作许可证全文。正式分发打包附件或镜像前，应基于最终制品补齐该清单、适用许可与 NOTICE，并核对 LGPL／MPL／GPL／AGPL 等所需的源代码或可替换性条件。已有的上游声明不得删除；新增根 MIT 不表示上述待验事项已经完成。
