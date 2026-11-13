# Backend BusyBox 的精确修复范围

2026-11-13，本记录对应 main 候选的 Backend 修复，不表示已发布新版本或切换日常环境。

Tomcat 11.0.27 候选的完整 Grype 报告仍有三条 `CVE-2025-60876 / Medium`：`busybox`、`busybox-binsh`、`ssl_client`，APK 版本均为 `1.37.0-r31`。原 JVM 检查只阻断 HIGH/CRITICAL/UNKNOWN，不能把这三条通过门槛解释为已审清。本次保留原报告、严重级别和版本元数据。

实际 `/bin/busybox` 原 SHA-256 为 `01a989eb…d42045`，属于未修复的 wget 实现。完整 Alpine `c3ef5d10e6ef…` 补丁集中，两个涉及 wget 的补丁只处理 Accept 头和 HTTPS helper；它们没有补上 HTTP request-target 边界。[上游已 review 的修复提案](https://lists.busybox.net/pipermail/busybox/2025-November/091840.html)与[精确 Alpine 构建配方](https://github.com/alpinelinux/aports/blob/c3ef5d10e6ef6528852c51f0564963e2f8c1be19/main/busybox/APKBUILD)支持这一源码判断。

本次复用仓库内[既有固定源码与补丁](../deployment/vendors/grafana-20261110/busybox/README.md)，实际重新编译得到同一受审二进制 `68dba7ae…2c697e`，替换 Backend 的公共 BusyBox dispatcher。`busybox-binsh` 原包只提供指向它的 `/bin/sh`。独立 `ssl_client.c` 是 TLS 字节转发器，不解析 URL 或构造 HTTP 请求目标；实际 helper 字节与此前受审对象相同。这是三个具体条目的不同处置，不是泛化排除整个 BusyBox 或 OpenSSL。

运行镜像同时保存完整原始源码包、44 个 Alpine 补丁、固定补丁、配置、构建脚本、Backend 集成配方、GPL/ISC 许可和构建身份，入口为 `/usr/share/deepresearch/backend-busybox/`。没有把自定义输出改称不存在的官方 Alpine 固定版本。当前实际固定字节、musl ABI、83 个源码/许可文件和三条完整原始告警由[新精确 profile](../deployment/security/backend-busybox-20261113-profile.json)核对；任何额外、缺失或改变的完整 Grype 条目都会失败。

为复用已有 vendor 子树而不复制维护一份源码，Backend 构建上下文改为仓库根目录。Compose 与安全矩阵均选取 `business-backend/Dockerfile`；配方只 COPY 明确的 Java、runtime 和 vendor 路径，没有整体 COPY 仓库。根 `.dockerignore` 排除本机 AGENTS、环境文件、缓存、Git 和私密命名材料。手动构建也须从仓库根执行：

```sh
docker build --platform linux/amd64 -f business-backend/Dockerfile -t deepresearch-backend-local .
```

独立云端应用编排也须叠加新的 Backend 构建入口。保持原冻结模板不变，从仓库根使用两个文件；新增 overlay 只替换 Backend 的 context/dockerfile，所有服务运行配置保持。下述是部署入口，不是本次已执行云端部署的记录：

```sh
docker compose --project-directory . --env-file .env.cloud -f deployment/compose/compose.cloud.yaml -f deployment/compose/compose.cloud.backend-build.yaml up -d --build --wait --wait-timeout 300
```

已用公开配置模板和不可用的合成凭据执行实际 Compose 配置合并，核对只有 Backend 的构建 context/dockerfile 两项变化，所有其他配置字段完全相同，明确 COPY 的源路径均存在；未读取本机云端凭据、构建或启动云端服务。[配置兼容核对](evidence/release-followup-20261113/backend-cloud-build-compatibility.json)保留原失败与工具边界。

当前实际构建通过原锁定包摘要和签名检查，使用本地既有锁定下载输入，并完成正常 applet、shell 和 loopback GET 检查；没有执行旧的恶意 URL 回归脚本。最终候选的只读、无网络 shell/wget 帮助/Java 启动检查通过。运行配置逐字段相同，Tomcat27 JAR 的全部 ZIP 成员字节与原候选相同；此前 69 项 Java 回归属于原相同成员字节的验证，并非本次又运行 69 项。

新标准 native/full Grype 扫描已完成：255 个 JRE 文件验证通过，native 0 条，full 保留三条 Medium，新精确固定代码和组件范围检查通过。官方当次选择的数据库经正常下载、独立受控导入、schema/时效/xxh64 和派生文件前后摘要验证；没有使用旧 DB 回退或修改扫描器阈值。最终完整 Trivy 原始漏洞 0 条、secret 0 条，实际 Engine 身份与候选一致。此前 mirror、GHCR 标准 Java DB 下载 EOF 和分片获取的过期签名 URL 403 均保留；最终同一官方归档通过整包摘要后，由官方客户端经只读 loopback 自然导入，未手改时效字段，最终扫描使用官方仓库和正常更新检查。[实际验收收据](evidence/release-followup-20261113/backend-busybox-acceptance.json)分别记录原始计数、技术处置和传输边界；不是零漏洞声明。

这不证明新版本已经发布、最终组合真实模型验收成功或日常环境已经更新。CodeQL 的本次变更仅续审 Backend 构建上下文和明确 COPY 边界；四个原始精确 finding 身份及 >=7 门槛不变，历史 SARIF 回放不冒充新扫描。
