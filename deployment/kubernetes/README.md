# Kubernetes 应用清单

`base/bootstrap-resources.json`、`base/resources.json` 与 `base/migration-job.json` 由 `python scripts/render_kubernetes.py` 生成；修改生成器后重新生成，CI 用 `--check` 核对。部署参数通过自己的 Kustomize overlay 配置，不手改生成文件，不必修改应用代码。此处不部署 PostgreSQL、Redis、Milvus、S3、OIDC 或扫描器。

1. 创建专用 `deepresearch` namespace。按 `docs/cloud-integration.md` 准备外部服务、恢复点和分权数据库角色；不要把管理员密码注入运行容器。
2. 在仓库已忽略的 `.env.kubernetes/` 目录复制三个 `*.secret.env.example`，填写真实值。创建 `deepresearch-python`、`deepresearch-java`、`deepresearch-migrator` Secret（`kubectl -n deepresearch create secret generic NAME --from-env-file=PRIVATE_FILE`）。Python 与 Java 使用同一个 Redis 数据库；所有 Java 副本的 `DEPLOYMENT_ID` 一致。不要把已填写的私密文件放回 `deployment/kubernetes/`。
3. 创建 `deepresearch-internal` Secret，key 为 `internal_service_token`（至少 32 字节随机值）；创建 `deepresearch-trust` Secret，提供 `postgres-ca.pem`、`redis-ca.pem`、`milvus-ca.pem`、`s3-ca.pem`、`otlp-ca.pem`、`oidc-ca.pem`。使用公共 CA 的服务也须提供其受信 CA bundle，或在 overlay 删除对应自定义 CA 配置。证书必须匹配实际 DNS。Python OIDC 通过 `OIDC_CA_CERTS` 显式加载该 CA；Java 的 Secret 示例提供 `JAVA_TOOL_OPTIONS`，其 `/run/certs/oidc-truststore.p12` 需由平台证书工具生成并作为同一 trust Secret 的额外 key，包含 OIDC 信任链及其他实际所需 Java 信任根。PKCS12 密码与文件要匹配，不能关闭证书检查。使用公共系统CA时，删除Python显式 `OIDC_CA_CERTS` 并清空Java这项即可。
4. 环境 overlay 替换所有 `REPLACE_` 值、Ingress host/class/TLS Secret、镜像的不可变 tag/digest，并为Java和Python配置相同非空 `PLATFORM_OPERATOR_SUBJECTS`。配置 issuer/JWKS、集合前缀、S3区域及已存在bucket；按需添加 S3 endpoint/SSE/KMS、OTLP TLS/认证、工作负载身份挂载。四个SA分别为 `deepresearch-python`、`deepresearch-java`、`deepresearch-web`、`deepresearch-migrator`；仅给Python SA配置访问所需Bucket／KMS的云身份，其他三个不继承该权限，均不默认挂Kubernetes API token。现有非空索引必须先按嵌入迁移流程验收，不能直接使用新默认指纹。
5. `NGINX_DNS_RESOLVER` 设置为集群 DNS Service IP，例如从 `kubectl -n kube-system get service kube-dns` 查询；不能沿用 Docker 的 `127.0.0.11`。为 Ingress controller namespace 添加 `deepresearch-ingress=true` 标签，为私网监控namespace添加 `deepresearch-monitoring=true` 标签，并为Prometheus Pod加 `app.kubernetes.io/name=prometheus`。NetworkPolicy 需要支持其语义的 CNI；基线限制入口，外部出站由平台防火墙/私网策略按实际端点限制。采集发现的只读RBAC配置见 `docs/production-observability.md`。
6. **先创建账号，再单独运行迁移 Job**：`kubectl -n deepresearch apply -f deployment/kubernetes/base/bootstrap-resources.json` 仅创建四个无API token的SA，不启动应用。将生成的迁移Job作为私密发布副本的输入，用 `kubectl set image --local` 配置同一版本Python镜像，再以 `kubectl patch --local --type=merge` 为该副本设置新的发布Job名；不要修改生成源文件。创建此Job后 `kubectl -n deepresearch wait --for=condition=complete job/YOUR_RELEASE_JOB --timeout=1200s`。Job使用独立迁移SA及迁移Secret，有界可写 `/tmp` 和 `/data`，不会获得运行时Python云身份。失败时停止发布，查看脱敏日志；不得把等待中或失败计为通过。每次发布使用新的Job名，保留旧Job证据，不先启动新应用再迁移。
7. 迁移完成后再 `kubectl apply -k YOUR_OVERLAY`，逐个 `kubectl rollout status deployment/NAME`。五个应用角色默认各2副本，滚动更新maxUnavailable=0，PDB保留1副本；需至少两个可调度节点才能验收节点故障，不因此承诺SLA。Worker探针检查真实就绪/心跳，preStop排空，45秒终止宽限包含30秒任务等待。
8. 在真实环境执行登录/刷新、TLS与权限、停用/交接、Redis降级、滚动升级、隔离恢复、告警送达和容量验收。离线渲染通过不代表集群或云服务已通过验收。

私有镜像仓库通过环境overlay的 `imagePullSecrets` 引用专用拉取Secret。AWS投射身份文件仅挂到Python Pod，不能放进四类服务共享的证书Secret。Java会将 `JAVA_TOOL_OPTIONS` 打到stderr，CA-only PKCS12的完整性密码不得复用任何数据库、云或API认证凭据。

私网 Prometheus 应发现所有 Pod IP：API8000、Java8080、研究/资料Worker8001的 `/metrics`；数据库事实源 gauges 跨副本取 `max`，事件 counters 才求和。不要把 metrics、Worker、Agent 或迁移容器暴露到公网。
