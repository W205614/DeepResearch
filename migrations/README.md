# 数据库迁移

PostgreSQL 结构由本目录的 Alembic 增量迁移维护，当前单一 head 为 `0019_embedding_migrations`。已执行的迁移保留原版本，新结构变化新增 revision。

从仓库根目录运行以下只读命令，不需要数据库连接：

```powershell
uv run alembic heads
uv run alembic history
```

Compose 的独立 `migrate` 作业通过 `scripts/migrate_services.py` 执行 `alembic upgrade head`，随后准备 LangGraph 检查点并收敛服务角色权限。`bootstrap-db` 先准备数据库身份，应用服务等待迁移成功。云部署使用独立迁移身份，流程见[部署与维护](../docs/deployment-security-operations.md)和[云平台手册](../docs/cloud-integration.md)。

升级前准备并验证匹配的恢复点，在隔离数据库中验证历史数据升级；不要修改日常配置来运行测试。回退须逐项核对迁移的 `downgrade` 和数据影响，切换旧镜像不能代替数据库恢复。SQLite 本地测试的兼容初始化由应用适配器维护，不代替 PostgreSQL 的 Alembic 迁移。
