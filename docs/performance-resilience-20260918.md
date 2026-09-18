# 性能、限流与雪崩隔离实测（2026-09-18）

## 结论与口径

本轮使用独立 Compose 项目 `dr-perf`，Java 固定为 1 CPU / 1 GiB，创建 64 个临时 OIDC 租户，并串联 PostgreSQL、Redis、Milvus、Java 控制面、Python API、ARQ Worker 与固定模型/搜索夹具。它证明本机故障边界和保护行为，不代表真实模型吞吐、生产 SLA 或多机容量。

原实现存在一个明确雪崩放大器：Java 对任意 Agent 转发请求最多同步等待 300 秒，没有代理舱壁或熔断；慢上游可以长期占用 Servlet 线程。单租户只有研究任务并发上限，没有通用请求速率上限；聚合流量也没有入口削峰。

本轮改为四层保护：

1. Nginx 在鉴权前按来源地址做 250 req/s、burst 250 的粗粒度削峰，超额返回 429；这是单机默认值，需要按真实 NAT、实例数和容量基线调整。
2. Java 对已认证主体执行 20 req/s、burst 40 的令牌桶，并限制 10,000 个活跃主体；不使用调用方可伪造的工作空间头作为桶键。这是单 JVM 边界，多实例的全局配额应迁移到网关或 Redis。
3. Java API 再设 32 个全局并发许可，Agent 代理单独设 8 个许可、25 ms 获取预算和 30 s 请求超时。上传改为流式转发，避免 `readAllBytes()` 的第二份完整堆拷贝。
4. Agent 代理连续 5 次可重试故障后熔断 15 s，只放行一个恢复探针；Outbox、研究准入和 Python Worker 继续使用各自的租约、队列、并发闸门、超时与供应商熔断。

## 实测结果

### 慢上游与故障上游

- 同时注入 24 个 2 秒 Agent 请求：8 个进入代理舱壁并在约 2.03 秒完成，16 个在 500 ms 内返回 503。
- 同期独立控制面维持 10 QPS：20/20 返回 200，P95 8.09 ms。慢 Agent 请求没有拖垮 Java 数据库控制面。
- 连续注入 503 后，第 6 个请求由熔断器直接拒绝；3 秒测试冷却后探针恢复为 200。
- 两个真实编排任务均完成，端到端共 4.70 秒。固定夹具累计 8 个任务的节点均值中，`web_scout` 约 299 ms 最慢，其次为 `validator` 244 ms、`router` 232 ms、`planner` 204 ms；并行节点耗时不能直接相加。
- Hikari 超时计数为 0；所有隔离容器在测试后仍为 running/healthy。

### 固定到达率 QPS 阶梯

单进程 Python 发压器在 500 QPS 以上自身先饱和，因此它的数据只保留给功能断言，不用于容量结论。最终容量数据来自 k6 固定到达率，`dropped_iterations=0`，每档 3 秒：

| 目标 QPS | 实际执行 | 200 | 保护性 429/503 | 其他错误 | P95 |
|---:|---:|---:|---:|---:|---:|
| 100 | 301 | 301 | 0 | 0 | 3.26 ms |
| 250 | 751 | 751 | 0 | 0 | 3.52 ms |
| 500 | 1501 | 998 | 503 | 0 | 2.78 ms |
| 800 | 2401 | 992 | 1409 | 0 | 46.95 ms |
| 1200 | 3601 | 986 | 2615 | 0 | 8.75 ms |

250 QPS 内没有保护性拒绝。500 QPS 起最先触发的是设计好的入口限流，不是 PostgreSQL、Worker 或 JVM 崩溃；超额请求被快速拒绝，已放行请求的尾延迟没有形成秒级堆积。3 秒档位包含 burst 250，因此 500 QPS 以上约放行 1,000 个请求；长时间稳态应以约 250 req/s/实例重新压测，不应从本结果外推生产吞吐。

## 日志与观测顺序

慢上游场景中首个业务保护信号为：

```text
component=agent_proxy phase=bulkhead_reject path=/api/status active=8
```

连续故障后为 `phase=circuit_open`；租户过载为 `component=api_rate_limit phase=rejected reason=rate`；聚合流量为 `reason=global_rate`；Java 并发许可耗尽为 `component=api_concurrency phase=rejected active=32`。以上日志按 10 秒采样，避免保护机制本身制造日志雪崩。Nginx 429 仅采样 1% 到 access log；完整数量以压测结果和指标为准。

新增或使用的核心指标：

- `deepresearch_business_agent_active`
- `deepresearch_business_agent_rejected_total{reason="bulkhead"}` 与 `{reason="circuit_open"}`
- `deepresearch_business_agent_duration_seconds`
- `deepresearch_business_rate_limit_rejected_total`
- `deepresearch_business_concurrency_active`
- `deepresearch_business_concurrency_rejected_total`
- `hikaricp_connections_*`、`tomcat_threads_*`、`http_server_requests_seconds_*`

## 复现

只允许在隔离项目上运行，正式 `deepresearch` 卷不参与：

```powershell
docker build -t deepresearch-verify-base .
docker build -f Dockerfile.verify -t deepresearch-verify .
docker compose -p dr-perf -f compose.verify.yaml -f compose.performance.yaml up -d --build --wait
docker compose -p dr-perf -f compose.verify.yaml -f compose.performance.yaml --profile performance run --rm --user 0 performance python scripts/load_system_performance.py --principals 64 --tokens-file /load/tokens.json
docker compose -p dr-perf -f compose.verify.yaml -f compose.performance.yaml --profile performance run --rm performance-k6
docker compose -p dr-perf -f compose.verify.yaml -f compose.performance.yaml down -v --remove-orphans
```

调参顺序应是：先用真实流量分布确定入口稳态容量，再调整 Nginx/Java 全局速率；随后按数据库等待、Tomcat busy、Agent 舱壁和 Worker 队列证据决定扩容。不要通过盲目增大线程池或连接池掩盖排队。
