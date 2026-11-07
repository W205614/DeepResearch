# 原生JVM与完整镜像安全检查

Java后端和Keycloak镜像同时执行完整Trivy扫描与 `scripts/check_native_jvm.py`。后者补充识别tar包安装的JRE，检查真实运行库、JVM身份及完整Syft／Grype报告。

## 工具与数据库

[工具清单](../deployment/security/native-jvm-tools.json)固定官方Syft 1.55.0、Grype 0.121.0的URL、长度与SHA256。复用缓存时再次核对归档和可执行文件；Windows使用单独锁定的官方可执行文件摘要。

Grype数据库使用清单中的官方静态时间戳URL。导入后核对展开数据库长度与SHA，`GRYPE_DB_AUTO_UPDATE=false` 保持本次输入稳定。数据库时效为7天；到期后更新官方URL、压缩及展开摘要、观测日期，再执行验证。缓存、下载、导入或时效异常会明确失败。

## 实际镜像检查

工具创建一个不启动的审计容器，以 `docker cp` 读取实际JRE，并逐文件及符号链接对照固定官方Temurin 21.0.13+9归档。JRE的release文本与实际ELF共同核验。

输出包括原生JVM和完整镜像两套SBOM／Grype报告。原生结果要求唯一JVM、正确版本、来源、ABI、路径和OpenJDK CPE；完整报告保留包清单和所有严重级别，HIGH／CRITICAL／UNKNOWN及忽略记录按门禁处理。

CI分别上传Trivy、原生SBOM、完整SBOM、配置和摘要。当前 `b8153f5` 的[Security 22个作业](https://github.com/W205614/DeepResearch/actions/runs/42370035403)已成功；早期验证见[历史记录](evidence/optimization-20261026/native-jvm-ci.json)。

## 本地运行

在有Docker及仓库Python依赖的环境中指定待检查镜像和新输出目录：

```powershell
uv run python scripts/check_native_jvm.py --image IMAGE --profile business-java --output-dir .cache/eval/native-jvm-unique
```

Keycloak使用 `--profile keycloak`。工具缓存与数据库路径可通过 `--tool-dir`、`--db-cache` 指定。审计容器保留created状态并记入summary，本地清理时核对所属容器和证据；CI runner由平台回收。
