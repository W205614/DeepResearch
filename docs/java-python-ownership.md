# Java / Python 职责与数据契约

Python负责LangGraph查询规划、检索、分析和报告生成；Java负责公开业务入口、身份权限、任务准入、治理、审核发布和Outbox。两侧通过内部API与共享PostgreSQL状态协作。

## 当前请求链

```text
Browser
  -> Java Spring Boot：OIDC、工作空间权限、任务准入、业务事务、Outbox、SSE
  -> Python FastAPI / Worker：LangGraph、模型与搜索、RAG、解析、检查点
  -> Java 持久化视图与 SSE
```

## 数据所有权

| 数据/能力 | 当前写入方 | 契约 |
| --- | --- | --- |
| `workspaces`、`memberships`、`workspace_limits`、`audit_logs` | Java | 管理空间、角色、限额、当前所有者与业务审计，保留原始创建者 |
| `platform_users`、`platform_audit_logs` | Java主导 | Java更新账号状态与撤销时间并追加治理审计；Python仅登记已验证主体及更新展示名、时间 |
| `embedding_migrations`、`embedding_migration_items` | 迁移工具 | Python日常角色只读，维护迁移单独使用迁移身份 |
| `threads` | Java | Java 创建、改名和校验归属；删除暂经鉴权代理调用 Python 清理关联的 Agent 数据 |
| `business_outbox` | Java | 只有 Java 创建、抢占、重试、置死信和人工重投 |
| `report_publications` | Java | Java 冻结报告和证据快照、执行双人审核、撤回与审计；Python 不修改发布状态 |
| `runs` 的创建、准入、`queued/cancelled` 生命周期命令及 `report_submitted` | Java | 同一工作空间用事务级锁保证并发额度和幂等判定；送审标记避免清理审核记录后重复提交旧任务 |
| `runs` 的执行状态、报告、来源、校验、调用预算和检查点关联字段 | Python Worker | Worker 只能处理已由 Java 创建的任务，并持续验证任务状态和执行所有权 |
| `events` | Java 与 Python | Java 写业务生命周期事件；Python 写 Agent 执行事件；事件只追加，不覆盖历史 |
| 用户偏好记忆 | Java | Java 校验用户所有权后执行 CRUD |
| 语义记忆、文档、附件、切块、向量索引、搜索缓存、检查点 | Python | 属于 Agent/RAG 执行层；Java 只负责公开入口鉴权和工作空间边界 |

`runs`和`events`为共享表。新增字段明确写入方，状态更新使用条件谓词、执行代与同一事务锁，防止取消、恢复和迟到结果互相覆盖。

## 报告审核与发布

- Agent 的 `runs.report` 是工作草稿，只有任务作者及当前工作空间管理员能读取；会话列表、报告导出、SSE 和任务附件遵守同一边界。历史作者字段为空的任务仅管理员可查看，不能代替作者提交审核。
- 作者只能将已完成、引用核验完整且有正文与来源的结果提交。Java 在事务中复制正文、来源定位与校验结果，生成内容 SHA-256；同一任务只产生一份审核快照。
- 另一位管理员批准后，工作空间成员通过 `/api/reports` 读取冻结版本。驳回与撤回保留原因；资料原件或当前索引失效时，快照仍可展示历史引用片段，但会标记原件当前不可访问。外部网页不会持续自动核验。
- 审核记录引用原任务，数据库禁止静默删除来源任务。管理员须先撤回已发布报告，再显式清理已撤回或已驳回的记录，随后才能删除会话或清理研究数据。Java 提交和 Python 删除使用同一工作空间数据库锁，避免并发时先检查后删除。

## Outbox 交付语义

- Java 在业务事务内同时写任务状态、事件和 Outbox 命令。
- 调度器通过 `FOR UPDATE SKIP LOCKED` 原子认领命令，网络调用期间使用有期限的租约，不长期持有数据库事务。
- 实例崩溃后，其他实例可以回收过期租约；暂时性错误按上限退避重试，永久错误或耗尽次数后进入死信。
- 管理员只能查看脱敏错误码，并可通过受 RBAC 保护的接口重投死信。
- Python 内部调度/中止入口必须保持幂等，因为“Agent 已执行但 Java 未收到响应”仍可能造成重复调用。交付语义为 **At Least Once**，由幂等键和执行代保护业务结果。

## 扩展约定

研究节点和RAG策略在Python维护；业务权限、角色、并发额度和正式发布状态在Java维护。新增跨服务状态时同步字段所有权、DTO、数据库授权及并发验证。当前部署及容量配置见[生产化验收](production-readiness.md)，新增业务围绕研究和证据生命周期展开。
