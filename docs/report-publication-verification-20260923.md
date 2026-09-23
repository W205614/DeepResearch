# 报告审核发布与单机验收（2026-09-23）

## 范围与实现

本轮在 Java 业务后端实现报告提交、管理员待审队列、批准、驳回、撤回、成员正式报告列表/详情/下载和显式清理。`0012_report_publication` 保存来源任务、作者、工作空间、冻结的 Markdown、来源及定位元数据、核验结果、SHA-256 内容哈希和审核记录。只有原任务作者可提交已完成、`validation.quality=complete` 且引用核验完整的结果；审核人必须是另一位管理员。发布范围为同工作空间成员，草稿范围为作者和管理员。历史作者不明的任务不能提交；被驳回后须新建研究任务。

旧任务详情、线程列表、SSE、会话报告导出和图片附件继续执行草稿可见范围。审核记录通过来源任务外键阻止静默删除；线程删除与工作空间研究数据清理还有冲突检查。原件版本或片段不再匹配时，快照保留证据但 `original_available=false`，界面显示原文当前不可访问。自动核验仅是提交门槛，批准仍需人工核对事实。Python 继续负责研究与资料处理，Java 负责发布事务、授权和审计。

## 功能与恢复验证

| 验证 | 本轮结果 | 边界 |
| --- | --- | --- |
| 默认 Docker Compose 完整重建及冒烟 | `0012_report_publication` 迁移，核心服务健康，OIDC、Java、Agent、Redis Worker、监控链路通过 | 单机环境 |
| Java Maven | 25/25，通过；其中 19 项 PostgreSQL Testcontainers | 覆盖跨工作空间、自审、低质量/重复提交、并发审核、快照冻结、旧详情/SSE/导出权限和资料版本变化 |
| Python | 237 通过，4 个条件性跳过；Ruff 通过 | Windows 系统临时目录权限异常，改用仓库 `.cache` 的 pytest 临时目录后全量通过 |
| 前端 | 12/12 单测，类型检查与生产构建通过 | 不替代浏览器验证 |
| 真实浏览器 OIDC | 企业流程 7 通过；3 项付费实时研究在套件中跳过、另行逐项通过 | 两个不同账号完成提交、批准、成员读取、资料变更、撤回和审计；实时 Redis 首次输出证据不足，重试通过 |
| 三份实时研究独立评分 | 3/3 通过，引用支持和覆盖均为 1.0 | 样本小且同模型图外评分，不是人工盲评 |
| 固定研究质量 | 29/30，整体门禁通过；引用支持率 99.12%，覆盖率 100%，关键题无失败 | `compare-1` 单题引用支持率 83.33%，仍是未通过案例；真实模型有输出波动 |
| 固定 RAG | 15 份资料、27 题：Recall@5 0.963，nDCG@5 0.9658，MRR@10 1.0；10 题答案门禁 10/10 | 固定语料，不是通用准确率 |
| 冷备恢复 | 隔离恢复 11 个命名卷、两份 PostgreSQL 逻辑备份、OIDC 与两组 Milvus 检索；253.47 秒 | 同机恢复观察值，不是异机容灾 RTO 承诺 |
| 组件故障 | 4/4 Testcontainers；Worker SIGKILL 恢复 56.25 秒，Redis/Milvus 恢复通过 | 使用确定性模型/搜索替身 |

本地生成的浏览器 trace、密钥、原始实时报告、备份和 `.cache/eval/*-20260923.json` 不提交仓库。可复现入口包括 `uv run pytest -q --basetemp .cache/pytest-final-report`、`uv run ruff check backend tests scripts main.py`、`mvn test`、`npm run test:e2e:enterprise`、`uv run python scripts/evaluate_research.py --debug-fixed-failures` 和 `scripts/smoke-enterprise.py`。付费实时浏览器题需显式设置 `E2E_LIVE=1`，文档题需 `E2E_DOCUMENTS=1`。

## 性能边界

压测使用隔离 Docker Compose、64 个临时 OIDC 用户、`GET /api/threads` 和确定性 Agent/上游替身。机器观测为 Docker 约 12 CPU、15.2 GiB 内存。数据只适用于此配置和短时单机测试。

| 场景 | 结果 |
| --- | --- |
| 30 秒恒定到达 200 QPS | 6000/6000 HTTP 200，已接纳请求 P95 3.86 ms |
| 30 秒恒定到达 250 QPS | 7501/7501 HTTP 200，已接纳请求 P95 3.38 ms |
| 30 秒恒定到达 300 QPS | 7748 HTTP 200，1253 次 429/503 保护性拒绝，其他状态 0 |
| 5 秒阶梯 300 QPS | 1500 次最终 200，但排队导致 P95 4328 ms、实际完成速率 171 QPS |
| 24 个慢上游并发 | 16 个 503 在 500 ms 内拒绝，8 个接纳；并行控制面请求 20/20 成功，P95 45.55 ms |
| 同租户 100 QPS、2 秒 | 79 个 200、121 个 429；熔断开启与恢复通过 |
| 研究执行链 | 2 个确定性替身任务均完成 |

因此本轮只能说在隔离、短时、只读控制面负载下，**200–250 QPS 完整接纳**；300 QPS 已进入保护性拒绝或排队区。真实研究任务受 `MAX_CONCURRENT_RUNS=2`、模型和搜索服务速度及预算影响，本轮没有测得可承诺的真实模型研究 QPS。压测脚本为 `scripts/load_system_performance.py` 和 `scripts/load-system-steady-k6.js`。
