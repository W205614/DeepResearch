# 生产监控与告警接入

本文对应企业内部、单区域多副本部署。代码与离线规则验证已实现；真实私网采集、通知送达和托管备份时效仍须在目标环境验收。本机备份计划保持停用，托管备份使用独立状态采集和恢复演练。

## 文件与启用方式

| 文件 | 用途 |
|---|---|
| `observability/alerts.yml` | 本地既有容量规则与应用生产规则；本机 `DeepResearchBackupStale` 保持 `vector(0) > 0` |
| `observability/alerts.production-cloud.yml` | 独立托管备份规则，仅在明确接入备份状态监控的环境加载 |
| `observability/prometheus.production.example.yml` | Compose／VPC 私网采集模板，逐个采集副本 |
| `observability/targets.production.example.json` | 副本地址清单，复制为 `targets.production.json` 后替换占位地址 |
| `observability/prometheus.kubernetes.example.yml` | Kubernetes Pod 自动发现，采集尚在排空但仍存活的 Pod |
| `observability/networkpolicy.production.example.yaml` | 监控账号只读发现 Pod，以及监控命名空间到应用指标端口的入口许可 |
| `scripts/cloud_backup_metrics.py` | 将云平台已观测的备份状态 JSON 转为 node_exporter textfile 指标 |

应用 `/metrics` 使用私网 HTTP。不要将指标端点或 Prometheus、Alertmanager 暴露到公网。VPC 模板把每个副本列成独立 target；不要使用入口负载均衡地址，否则会遗漏进程计数器或把多个副本误认为同一个进程。保持所有同一平台副本的 `deployment` 标签相同，不同生产环境使用不同值。

VPC 模板中的托管备份 exporter 和 Alertmanager 采用 HTTPS：挂载可信 CA 到 `/run/certs/monitoring-ca.pem`，凭据分别挂载到 `/run/secrets/backup-exporter-token` 和 `/run/secrets/alertmanager-token`；证书轮换后按所用服务要求重载。已有服务使用其他认证方式时，修改 Prometheus 标准 `authorization`／`tls_config` 配置即可；不要把秘密写进 target 标签或 Git。

Kubernetes 模板假设应用命名空间为 `deepresearch`、监控命名空间为 `monitoring`，Prometheus ServiceAccount 为 `prometheus`。替换实际名称，给监控命名空间设置 `deepresearch-monitoring=true`，给 Prometheus Pod 设置 `app.kubernetes.io/name=prometheus`，再应用配套 Role、RoleBinding 和 NetworkPolicy。发现权限仅限应用命名空间 Pod 的 get/list/watch。已有默认拒绝出站策略时，另外允许 Prometheus 访问 Kubernetes API、应用指标、备份 exporter 与 Alertmanager；这些端点由部署平台确定。

Java 用 Spring 标准结构化配置启用请求直方图，使 `http_server_requests_seconds_bucket` 可用于 P95 规则：

```dotenv
SPRING_APPLICATION_JSON={"management":{"metrics":{"distribution":{"percentiles-histogram":{"http.server.requests":true}}}}}
```

如果已有 `SPRING_APPLICATION_JSON`，合并上述对象；不要覆盖已有条目。Boot4对相似平铺变量的relaxed binding会歧义绑定到 `percentiles` 映射并导致启动失败，因此使用上述结构化配置。目标环境需确认请求后实际出现 bucket。

使用已有 `scripts.ops_metrics:create_app` 作为只读容量 exporter 时，配置独立 `MONITOR_DATABASE_URL` 并使用 `dr_monitor` 最小权限角色；云环境的 target 设置 `host_probes="disabled"`，避免把未配置的工作站探测误报为云主机故障。该标签只关闭本机探测时效规则，不关闭数据库容量告警。

## 指标含义与告警处置

数据库状态在多个 API／Worker 副本中重复采集，规则按 `max by (deployment)` 聚合，包括队列、扫描失败、死信、Outbox 和持久化心跳，排除重复采样。各进程实际发生的失败、预算拒绝和代理过载事件则对 `increase(counter[窗口])` 求和。实例就绪、排空和 Redis 可达性保留其进程语义。

| 情况 | 实际指标与门槛 | 首要处置 |
|---|---|---|
| 接单或恢复失败 | Java POST 接单、intake 确认、恢复返回 5xx，5 分钟内出现并持续 1 分钟 | 查全局 waiting20 容量、SQL 与业务日志；按原 client_request_id 核对是否已接单后重试 |
| Redis 只读降级 | 任一 Java 副本 `deepresearch_business_redis_available=0` 持续 1 分钟 | 恢复私网、ACL、TLS、主端点；确认历史可读、业务修改 503；不要绕过共享限流 |
| 任务积压 | SQL 队列超过 10 持续 5 分钟，或最旧 queued 超过 120 秒 | 查 Worker 是否就绪、全局执行5／空间执行2限额、供应商阻断和预算 |
| Outbox 迟滞或死信 | 最旧消息超过 120 秒持续 2 分钟；dead 深度大于0持续1分钟 | 查 Agent 内网调用、执行代和租约；用现有管理员死信重试接口，不手改状态 |
| Worker 不就绪 | 非排空实例 ready=0 持续2分钟；同角色无就绪实例持续1分钟为 critical | 查本机 Worker 健康状态、SQL、Redis与进程日志，恢复后核对持久化任务收敛 |
| 排空未结束 | `deepresearch_worker_draining=1` 超过10分钟 | 核对终止宽限期和执行取消；保留检查点，按恢复流程处理，勿删除业务证据 |
| 预算耗尽 | 周期预算拒绝 counter 增加，或任务 `budget_exhausted` 失败增加 | 缩小问题、等待周期重置或由有权限者授权调额；不要自动放大预算 |
| 扫描失败 | SQL `documents.status='scan_failed'` 数大于0持续5分钟 | 查 ClamAV、对象存储和扫描作业错误；用支持的恢复或重新上传流程，禁止跳过扫描 |
| 供应商或心跳异常 | 阻断供应商数量大于0、最差运行心跳超过45秒 | 查供应商凭据／配额、Worker 所有权和恢复事件 |

`DeepResearchAdmissionFailure` 依赖 Spring 请求观测的匹配路由标签；在安全过滤器中提前返回的 Redis 降级由专门 Redis 告警覆盖，不能仅凭接单计数器断言不存在拒绝。指标不包含用户、空间、任务 ID、研究正文、原始 URL 或密钥标签。

## 托管备份状态桥接

云平台可直接通过其 Prometheus exporter 和配置映射提供下列同义指标，也可由平台定时查询备份服务后生成 JSON，调用仓库转换工具；应用代码不需要修改。此接入使用下面的状态协议，云平台查询和跨资源恢复由部署方配置。

每项必须监控的资源使用一个非秘密别名，至少覆盖实际恢复方案依赖的 PostgreSQL、证据对象、身份配置及必要索引。JSON 协议见 `observability/cloud-backup-status.example.json`：

- `version` 为1，`observed_at` 是平台实际查询成功的 epoch 秒，使用原始观测时点。
- `resource` 为唯一 `[a-z][a-z0-9_-]{0,63}` 别名，最多32项；禁止填 endpoint、bucket 路径或主体标识。
- `last_success_at` 是平台确认备份任务成功的 epoch 秒；尚无成功记录用0。它不能晚于 `observed_at`。
- `rpo_seconds` 是显式策略，整数范围60秒至1年；示例24小时需要按组织策略确认。

示例时间为0，运行时由真实平台观测值替换。由部署平台的备份查询作业写入真实状态文件，再运行：

```bash
uv run python scripts/cloud_backup_metrics.py \
  --input /run/backup-status/provider.json \
  --output /var/lib/node_exporter/textfile_collector/deepresearch-cloud-backup.prom
```

输出目标目录须已存在，并启用 node_exporter 的 textfile collector。转换器先校验全部输入，再原子替换 `.prom`；失败会非零退出并保留旧文件，旧 `observed_at` 最终触发监控过期。每5分钟或更频繁更新状态。将该 exporter 作为 `job="deepresearch-cloud-backup"` 采集，并加载独立云规则：

| 指标 | 含义 |
|---|---|
| `deepresearch_cloud_backup_required{resource}` | 明确列入监控的资源，值1 |
| `deepresearch_cloud_backup_observed_timestamp_seconds{resource}` | 云平台状态观测时间 |
| `deepresearch_cloud_backup_last_success_timestamp_seconds{resource}` | 最近成功备份时间 |
| `deepresearch_cloud_backup_rpo_target_seconds{resource}` | 此资源RPO秒数 |

规则分别检查 exporter 不可达、完整清单缺失、单项字段缺失、15分钟没有新观测、成功备份超出RPO。删除清单项目属于监控范围变更，须和恢复方案一起审核。仅在云监控配置加载这组规则，不修改本机 `DeepResearchBackupStale` 的禁用表达式。

## 验证与验收

本地离线验证：

```bash
uv run python scripts/check_observability.py
uv run pytest -q tests/test_cloud_backup_metrics.py
```

第一条复用维护中的 Prometheus 镜像，仅运行网络隔离、只读根文件系统、临时目录有界的一次性 `promtool` 容器；检查37条规则、三份采集配置语法及真实 PromQL 用例，不启动或停止日常应用。CI 入口为 `.github/workflows/observability.yml`，使用版本与摘要固定的官方 promtool 镜像执行相同验证。`--syntax-only` 不验证示例证书、秘密或目标端点确实存在；目标环境替换配置后必须再运行完整 `promtool check config`。

实际发布前逐项核对：所有预期副本 target 可见且 `up=1`；队列 gauge 跨副本相同且未相加；P95 bucket存在；受控就绪失败、排空、积压、预算拒绝、扫描失败和云备份过期可触发对应告警；最后通过现有 Alertmanager／告警中继流程验证真实通知送达与恢复通知。将离线用例与目标环境的实际采集、触发和送达结果分别留存。

Prometheus 标准配置与规则测试语法参考：[配置文档](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)、[规则单元测试](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/)。
