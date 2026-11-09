# 应用容器 Packages

应用制品分成三个 Linux/amd64 包。Agent 包还用于 Worker、资料 Worker、迁移、数据库初始化、容量指标和告警中继；角色命令由原 Compose 服务配置决定。

| 应用 | 固定版本示例 |
| --- | --- |
| Python Agent | `ghcr.io/w205614/deepresearch-agent:v0.1.1` |
| Java 业务后端 | `ghcr.io/w205614/deepresearch-backend:v0.1.1` |
| Web | `ghcr.io/w205614/deepresearch-web:v0.1.1` |

应用包与第三方服务分开维护。PostgreSQL、Keycloak、Milvus、MinIO／Silo、观测组件等继续采用原固定来源；三个应用包不是离线、自包含的完整环境。首次安装仍需取得这些依赖及供应商凭据。

## 获取与启动

新安装使用制品 Release 对应的源码标签，取得相同版本的 Compose 覆盖和工具；需要 Git、Python 3.11+、Docker Linux/amd64 和 Compose。初始化后按[配置说明](source-release.md)填写本机私密凭据。固定 MinIO 镜像及第三方构建型服务仍需准备，三个应用镜像从 GHCR 获取：

```powershell
git clone --config core.autocrlf=false --branch artifact-applications-v0.1.1-r1 https://github.com/W205614/DeepResearch.git
cd DeepResearch
python scripts/local.py init
# 填写本机 .env 的模型／嵌入凭据及可选重排配置后继续。
python scripts/local.py check
python scripts/fetch_approved_image.py
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml -f deployment/compose/compose.packages.yaml build postgres keycloak-postgres etcd milvus keycloak storage-init
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml -f deployment/compose/compose.packages.yaml pull agent backend web
docker compose -f compose.yaml -f deployment/compose/compose.core.yaml -f deployment/compose/compose.packages.yaml up -d --no-build --wait --wait-timeout 600
```

第三方构建型服务首次安装仍按原运行说明准备，不能用上述应用包替代。已有部署保留原项目名、`.env`、端口／网络覆盖文件、账号及业务卷；在原配置后追加应用包覆盖，核对已有迁移与恢复条件，不用新安装参数覆盖日常环境。日常暂停使用同一套 Compose 参数的 `stop --timeout 60`。

版本标签首次发布后不覆盖；发布收据记录各包的 registry digest 和 image config ID。正式部署优先使用收据中的 `ghcr.io/...@sha256:...`，不能仅凭 `v0.1.1` 或 `latest` 判断字节身份。包初次上传时的默认私有状态与最终公开状态分别验证；公开包应能在没有登录凭据的环境中拉取。

## 制品来源与许可材料

镜像内 `/usr/share/deepresearch-distribution/` 提供原创 MIT LICENSE、第三方声明、实际依赖／许可清单、保留的版权与许可文本，以及对应源码的准确获取说明。整个镜像不统一改为 MIT；第三方组件保留自身许可。

对应的 [应用制品与源码材料 Release](https://github.com/W205614/DeepResearch/releases/tag/artifact-applications-v0.1.1-r1) 提供：

- `publication-manifest.json`：最终源码、三个实际 image ID、分片长度／SHA-256、归档 SHA-256 和本地验收状态。
- 应用镜像归档分片：由经过验收的同一候选生成，发布工作流只校验、加载和推送，不重新构建。
- 对应源码与许可材料归档分片：实际 Debian／Alpine 版本的源码、补丁和构建配方，CPython／Temurin、相关 Python／Java 组件源码及原版权材料；具体覆盖与摘要见材料清单。
- 发布收据：三个包的不可变拉取地址及与候选 image ID 的对应关系。

镜像从无业务挂载、无运行过程的临时容器导出最终文件系统，排除 Docker 自动生成的主机名、hosts、resolv.conf 和运行标记，然后加上分发材料；保留原应用文件、权限、入口、环境、健康检查和信号设置。最终文件系统与新增许可文件分别核验，重新执行制品安全与运行验证。历史基础镜像中已被删除／覆盖的内容不会被作为隐含旧层再次分发。

源码版 `v0.1.1` 保留原标签。镜像的原运行源码 revision 和本次打包配方 revision 分开记录，不能把补充许可材料写作原应用重新编译。历史真实 API 证据的复用必须核对实际运行字节和配置；发布／拉取成功不代表模型质量、目标云环境、容量或异机恢复通过。

## 维护者发布

先确定源码提交、实际候选与许可材料，完成本地安全和运行验收，生成归档分片与清单。为相同提交建立独立应用制品 Release 草稿，核对所有分片后正式发布。保留失败及未知结果，不上传私密 `.env`、业务数据、恢复包、本机 AGENTS.md 或本地证据目录。

等待该提交 CI／Security 成功后，显式运行 [Publish verified application packages](../.github/workflows/publish-application-packages.yml)，传入制品 Release 标签与已验收清单的 SHA-256。工作流使用仓库自己的 `GITHUB_TOKEN` 和 `packages: write`，不要求增加本机个人令牌权限；推送前检查命名空间及既有版本，拒绝覆盖不同字节的版本。

发布后核对包可见性，按预期身份实际拉取 digest，验证 config ID 与候选一致，再更新约定日常环境、验收并恢复原启停状态。收尾后才清理已经结束使用的普通临时文件；许可和对应源码材料继续随制品保留。
