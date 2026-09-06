# DeepResearch 企业演示版运维说明

## 边界

此编排用于个人作品集和面试演示。它展示 OIDC、工作空间权限、审计、异步任务和观测边界，不代表跨地域容灾或正式 SLA。

## 启动与检查

1. 从 `.env.example` 创建 `.env`，生产演示使用 `AUTH_MODE=oidc`。
2. 启动 Compose 后，先访问 `/livez`，再访问 `/readyz`。后者会检查向量服务和 Redis 队列。
3. Keycloak 中创建 OIDC 客户端，令牌 audience 必须为 `OIDC_AUDIENCE`；后端只接受 issuer、audience、过期时间和 JWKS 都通过校验的 JWT。
4. 首次登录自动获得一个个人工作空间管理员权限。成员管理、审计和导出要求 admin；researcher 可以研究和上传；viewer 只读。
5. 每次重新构建后运行 `.scriptssmoke-enterprise.ps1`。该检查不发起模型研究，因此不会产生模型或搜索费用；它只验证 OIDC 发现、迁移、队列依赖、Worker、`/readyz` 与遥测 Collector。
6. Keycloak 用户保存在 `keycloak-data` 命名卷。保留本机账号时执行 `docker compose down`；`docker compose down -v` 会删除它以及研究数据。

## 备份、恢复与回滚

- PostgreSQL：使用 `scripts/backup.ps1` 导出逻辑备份；恢复前先在隔离环境执行 `scripts/restore.ps1`。
- 研究附件和 LangGraph 检查点位于 `research-data` 卷，应和 PostgreSQL 备份按同一时间点保存。
- 发布前执行迁移；失败时回滚应用镜像，数据库回滚只在该迁移明确提供 downgrade 且已验证时执行。
- 每季度演练：从空环境恢复 PostgreSQL、附件卷和 Milvus 数据，验证一个已完成报告可读取、一个 interrupted 任务可继续。

## 容量与告警起点

个人演示默认每工作空间并发 2 个研究任务。将队列积压、失败率、P95 节点延迟、模型 Token 与每日成本作为仪表盘起点；报警阈值必须用真实运行数据校准。
Grafana 仅开放在 `http://localhost:3000`，并自动配置容器内 Prometheus 数据源。当前它首先用于确认服务可达与采集链路；任务级成功率、P95、失败类别、Token 与成本仍由工作台的“本地研究指标”展示。将这些按工作空间聚合后再进入 Prometheus 是下一阶段，避免把研究主题、用户或来源地址做成监控标签。
