# Java / Python 职责与数据契约

本项目的主体仍是 Python LangGraph 编排查询、检索、分析和报告生成。Java 的职责是把这条不确定的 Agent 执行链放进可鉴权、可并发控制、可取消恢复、可审计的业务任务中，而不是重写 Agent。

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
| `workspaces`、`memberships`、`workspace_limits`、`audit_logs` | Java | Python Agent 不修改租户、角色、限额和业务审计 |
| `threads` | Java | Java 创建、改名和校验归属；删除暂经鉴权代理调用 Python 清理关联的 Agent 数据 |
| `business_outbox` | Java | 只有 Java 创建、抢占、重试、置死信和人工重投 |
| `runs` 的创建、准入、`queued/cancelled` 生命周期命令 | Java | 同一工作空间用事务级锁保证并发额度和幂等判定 |
| `runs` 的执行状态、报告、来源、校验、调用预算和检查点关联字段 | Python Worker | Worker 只能处理已由 Java 创建的任务，并持续验证任务状态和执行所有权 |
| `events` | Java 与 Python | Java 写业务生命周期事件；Python 写 Agent 执行事件；事件只追加，不覆盖历史 |
| 用户偏好记忆 | Java | Java 校验用户所有权后执行 CRUD |
| 语义记忆、文档、附件、切块、向量索引、搜索缓存、检查点 | Python | 属于 Agent/RAG 执行层；Java 只负责公开入口鉴权和工作空间边界 |

`runs` 和 `events` 目前是共享表，这是渐进式拆分，不是数据库隔离的微服务。新增字段前必须说明唯一写入方；不得让 Java 与 Python 对同一状态做无条件覆盖。

## Outbox 交付语义

- Java 在业务事务内同时写任务状态、事件和 Outbox 命令。
- 调度器通过 `FOR UPDATE SKIP LOCKED` 原子认领命令，网络调用期间使用有期限的租约，不长期持有数据库事务。
- 实例崩溃后，其他实例可以回收过期租约；暂时性错误按上限退避重试，永久错误或耗尽次数后进入死信。
- 管理员只能查看脱敏错误码，并可通过受 RBAC 保护的接口重投死信。
- Python 内部调度/中止入口必须保持幂等，因为“Agent 已执行但 Java 未收到响应”仍可能造成重复调用。因此这是 **At Least Once**，不是 Exactly Once。

## 不允许跨越的边界

- Java 不实现 LangGraph 节点、查询规划、模型调用、RAG 排序或报告写作。
- Python 不决定工作空间权限、业务并发额度、最终取消状态或审计结果。
- 不因技术展示增加与研究场景无关的工单、商城、支付等业务模块。
- 在完成独立 schema、版本化 DTO 和跨服务容量验证前，不宣称为完全独立微服务或生产高可用架构。
