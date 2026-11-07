# 本地运行与维护

## 使用方式

默认Compose用于个人本地研究和团队功能验证。外部服务与多副本部署见[接入手册](cloud-integration.md)。个人功能交付已可进入按需维护阶段，当前验证基线和启动入口见[README](../README.md)。

自动备份按本机策略停用；升级前准备并核验手动恢复点。下文保留备份与隔离恢复工具的使用步骤。

## 启动与检查

1. 从 `.env.example` 创建 `.env`，为企业演示设置 OIDC、数据库和模型配置。
2. 按[快速部署](../README.md#本地快速部署)加载批准归档，再运行 `docker compose up -d --build --wait`。
3. 执行 `.\scripts\smoke-enterprise.ps1`。该检查不会调用模型或搜索服务，会验证 Web、Keycloak OIDC、Alembic、Redis、`/readyz`、Worker 指标、Prometheus、Grafana 与 OTel Collector。
4. 首次登录会创建个人工作空间管理员。管理员可管理成员、审计、导出和删除；researcher 可研究与上传；viewer 只读。
5. `migrate` 显示 `Exited (0)` 表示 Alembic 已成功完成，必须保留。停止服务使用 `docker compose down`，不要使用 `down -v`，否则会删除本机账号与研究数据。

## 资料扫描与索引

上传资料先进入隔离区并由 ClamAV 扫描。只有 `ready` 的资料可检索或引用；`quarantined` 和 `scan_failed` 不能建立索引、检索、研究或导出。资料索引由可恢复任务处理，服务重启后会重新处理仍处于 `indexing` 的已通过扫描资料。

## 备份、恢复与回滚工具（当前本机停用）

- 在维护窗口等待在途任务结束后运行 `scripts/backup.ps1`。脚本自行短暂停止服务、归档数据和精确镜像、保存私有部署快照及哈希，最后恢复原先运行状态；具体支持范围和最新实际验证见[生命周期说明](evidence-lifecycle.md)与[交付记录](lifecycle-delivery-20261020.md)。
- 在新的隔离项目恢复，禁止覆盖原卷。推荐执行备份包内的 `restore.py restore --path <备份绝对目录> --project dr-restore-<唯一名称> --subnet <未占用私网段>`。`scripts/restore.ps1 -BackupPath <目录>` 也只允许新项目，`-ReplaceVolumes` 会明确拒绝。
- 恢复检查账号、权限、正文/引用 SHA、检索及研究接单后取消；不会执行真实外部模型研究。保留本次结果与恢复卷，按对应版本核对。
- 发布前执行迁移；应用回滚可回退镜像。数据库回滚只在该迁移明确提供 downgrade 且已验证时进行。

## 容量与告警起点

个人演示默认每工作空间并发 2 个研究任务。Grafana 只聚合匿名指标：服务可用性、队列积压、成功率、失败类别、节点 P95 和 Token。阈值按真实运行校准；指标标签使用有限类别，用户、主题、文件名、URL和错误正文保留在受权限保护的业务记录中。
