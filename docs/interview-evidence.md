# 面试证据与源码入口

本文按请求链、并发故障和验证入口整理源码，便于准备项目讲解。

## 一次请求如何完成

浏览器通过Keycloak授权码与PKCE登录 → Java校验账号、工作空间权限和共享限流 → `ResearchCommandService`在事务中检查幂等、准入和预算，写入任务与Outbox → Python内部API接受调度 → Worker认领执行代，恢复LangGraph检查点 → 以当前执行所有权提交结果 → SSE按持久化事件编号重放，前端读取报告与引用。

业务入口：[ResearchController.java](../business-backend/src/main/java/com/deepresearch/business/api/ResearchController.java)、[ResearchCommandService.java](../business-backend/src/main/java/com/deepresearch/business/service/ResearchCommandService.java)。执行入口：[runtime.py](../backend/services/runtime.py)、[graph.py](../backend/research/graph.py)、[worker.py](../backend/worker.py)、[enterprise.e2e.ts](../frontend/e2e/enterprise.e2e.ts)。

## 可以现场解释的故障案例

| 触发 | 修复前风险 | 当前行为与证据 |
| --- | --- | --- |
| 上传开始索引时删除 | 迟到向量/SQL 写入令资料复活 | 资料版本作废，发布时重新检查版本；检索只接受当前 SQL chunk；真实 Milvus 验证无残留。见 documents.py、test_components.py |
| 队列满时重试同一请求 | 先检查额度导致原请求也被拒绝 | 先返回匹配原任务，修改内容则冲突；SQLite/PostgreSQL 统一业务异常。见 test_consistency.py |
| 同时多次调用搜索 | 并发检查越过额度，删除研究返还次数 | 事务原子预占任务和 UTC 日额度，任一失败全部回滚；独立台账不随研究删除。见 postgres.py、0004_consistency.py |
| Worker 被终止，旧进程迟到 | 重复执行或覆盖新结果 | 心跳/终态带尝试版本；持久化任务对账补投；真实组件恢复后只有一个done。见 verify_components.py |
| Redis 投递失败 | 数据库已接受但队列无任务 | 接受记录留在数据库，周期扫描补投，稳定队列编号去重；真实 Redis 停启通过 |
| 报告把“未完成 42 人”写成“完成 42 人” | 关键词相同而错误放行 | 独立支持判定覆盖否定、主体、日期及范围；固定错误反例必须拒绝。见 quality_gate.py、evaluate_answers.py |

## 设计取舍

1. SQL 是任务和资料有效性的权威，向量库负责候选检索。跨数据库无法一次事务提交，采用版本校验与补偿清理；这增加清理状态，但避免依赖分布式事务。
2. 采用持久化任务表对账和 Redis 去重，未引入新消息中间件。保证旧尝试不能提交最终结果，不承诺模型外部调用恰好一次；故障窗口可能重复调用并消耗额度。
3. 幂等语义绑定工作空间、请求编号及请求内容。它解决重试，不能替代权限验证或无限制恢复。
4. 质量阈值依赖版本化小题集和模型辅助评分。历史评分保留原题集与模型配置；当前OpenAI已完成四类连通性验证，完整质量评测另用固定集和独立预算。
5. 恢复演练核对数据、账号权限、报告哈希、引用和下载，记录实际恢复时点与用时。目标云平台的身份和异机恢复单独验证。

最新代码与验证见[README](../README.md)和[生产化证据](evidence/production-20261107/README.md)，早期记录见[2026-09-10收尾验收](interview-delivery.md)。面试时可从上述任一故障解释：哪个状态是权威、在哪里检查版本、失败后谁来补偿、测试如何制造竞争、仍有哪些不能保证的边界。
