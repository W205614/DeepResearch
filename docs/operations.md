# DeepResearch 企业演示版运维说明

## 边界

此编排用于个人作品集和面试演示。它展示 OIDC、工作空间权限、审计、异步任务、扫描隔离和观测边界，不代表跨地域容灾或正式 SLA。

## 启动与检查

1. 从 `.env.example` 创建 `.env`，为企业演示设置 OIDC、数据库和模型配置。
2. 启动企业编排：`docker compose -f compose.yaml -f compose.enterprise.yaml up -d --build --wait`。
3. 执行 `.\scripts\smoke-enterprise.ps1`。该检查不会调用模型或搜索服务，会验证 Web、Keycloak OIDC、Alembic、Redis、`/readyz`、Worker 指标、Prometheus、Grafana 与 OTel Collector。
4. 首次登录会创建个人工作空间管理员。管理员可管理成员、审计、导出和删除；researcher 可研究与上传；viewer 只读。
5. `migrate` 显示 `Exited (0)` 表示 Alembic 已成功完成，必须保留。停止服务使用 `docker compose down`，不要使用 `down -v`，否则会删除本机账号与研究数据。

## 资料扫描与索引

上传资料先进入隔离区并由 ClamAV 扫描。只有 `ready` 的资料可检索或引用；`quarantined` 和 `scan_failed` 不能建立索引、检索、研究或导出。资料索引由可恢复任务处理，服务重启后会重新处理仍处于 `indexing` 的已通过扫描资料。

## 备份、恢复与回滚

- 在停止演示服务后运行 `scripts/backup.ps1`。它导出 PostgreSQL SQL、研究附件、Milvus、Redis、Keycloak 和 Grafana 卷，并写入 SHA-256 清单。
- 先在隔离环境验证备份，再使用 `scripts/restore.ps1 -Input <备份目录> -ReplaceVolumes` 恢复。该参数是显式确认，会替换当前 DeepResearch 命名卷。
- 恢复后启动企业编排并运行 `scripts/smoke-enterprise.ps1`。每季度至少演练一次恢复。
- 发布前执行迁移；应用回滚可回退镜像。数据库回滚只在该迁移明确提供 downgrade 且已验证时进行。

## 容量与告警起点

个人演示默认每工作空间并发 2 个研究任务。Grafana 只聚合匿名指标：服务可用性、队列积压、成功率、失败类别、节点 P95 和 Token。阈值需要由真实运行数据校准；不得把用户、研究主题、文件名、URL 或错误正文作为指标标签。
