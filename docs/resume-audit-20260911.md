# 项目重点与整体复测核对（2026-09-11）

2026-09-12 更新：术语边界修复后的单次完整固定研究复测 **30/30 通过**，引用支持率 **100%（297/297 条）**、应答覆盖率 **100%**，固定研究质量门禁通过，见[新复测记录](research-full-20260912.md)。下方“本次数据”及失败解释保留 2026-09-11 历史事实；“可直接采用的四条”已更新质量指标，其余指标仍为 2026-09-11 结果。

代码基线：`e5310e30feefd73d237ee409b9691d137614ead0`。环境：Windows、Docker Desktop、单机 Compose。开始时工作树干净；本次只更新结果和文档，不修改业务实现、不重跑失败题挑选更好成绩。运行中 backend / worker 各 33 个 Python 文件经换行归一化后的哈希与当前源码一致。

结论：四条重点符合 AI 应用后端方向，覆盖研究编排、检索、质量边界和异步可靠性。但本轮质量门禁失败，不能称整体全部通过。当前默认企业工作台还具备 OIDC、工作空间角色、资料隔离扫描和观测链路；四条适合简历聚焦，并非完整能力清单。

## 本次数据

| 检查 | 本次结果 | 解释 |
| --- | --- | --- |
| 后端 pytest | 108 通过、4 跳过，89.72 秒 | 4 项为另行执行的真实组件测试 |
| 真实组件 | 4/4 通过 | 独立 `dr-verify` Compose |
| Ruff / 前端 | 静态检查通过、Vitest 4/4、类型检查及构建通过 | 当前源码 |
| 固定路由 | 7/7 | 离线 demo 替身，不是实模路由准确率 |
| 独立回答门禁 | 10/10 | 7 个生成题、3 个固定错误反例，真实模型 |
| 固定完整研究 | 29/30；关键题 `negation-2` 失败 | 真实模型 + 固定合成网页证据；门禁不通过 |
| 引用支持率 | 99.66%，294/295 条 | 图外同模型另行评分，不是人工盲评 |
| 应答覆盖率 | 100% | 按应回答题预期事实覆盖率平均计算 |
| 真实嵌入检索 | Recall@5 96.88% → 100%；nDCG@5 94.46% → 96.76% | 14 份资料、16 个标注问题，两轮质量一致 |
| 业务负载 | 5 客户端、300 秒、4576 请求、386/386 任务完成 | 非预期失败 0；每个任务一个 done 事件且完成任务唯一 |
| 负载 P95 | 控制/读取 243.79 ms；研究 3139.37 ms；索引 1738.21 ms；排队 1351.61 ms | 模型、搜索、64 维嵌入为可控替身；存储组件真实 |
| 故障恢复 | Worker 53.50 秒；Redis 投递恢复和 Milvus 就绪恢复通过 | 单次演练，不是 RTO/SLA 承诺 |
| 企业冒烟 | 通过 | Web、OIDC、迁移、Redis、Worker、Prometheus、Grafana、Tempo、Loki、OTel |
| 常规浏览器 | 2 通过、4 条件跳过 | 随后显式启用资料和联网场景，结果见下节 |
| 完整浏览器 | 6/6 通过、0 跳过，9.7 分钟 | 真实 OIDC、3 个联网主题、资料生命周期与 viewer 权限；临时身份清理成功 |
| 实时报告图外评分 | 3/3 通过，引用支持率 100%（83/83 条）、覆盖率 100% | 仅本次 PostgreSQL、Redis、OIDC 三个公开主题，不抵消固定 30 题失败 |

检索时延见 [最新检索记录](../eval/benchmark_results.md)；完整脱敏统计与题目结果见 [audit-20260911.json](../eval/audit-20260911.json)。本机并行运行了部分验证，吞吐和时延不能与旧轮次直接解释为性能退化或优化。测试用 Compose 容器、网络和卷已清理，日常服务保留。备份恢复与远程 CI 本次未重跑，不沿用历史记录称为本次通过。

## 主张核对

| 原主张 | 核对与采用口径 | 源码证据 |
| --- | --- | --- |
| 10 个同进程职责角色 | 成立，10 个包含 chat；单条研究路径不执行全部角色 | `backend/research/agents.py` 的 AGENTS，`backend/research/graph.py` 图边 |
| SSE 记录交接产物 | 事件记录发送方、接收方和产物字段名；结构化产物由 ResearchState 交接，并非 SSE 记录全部正文 | `AgentRuntime.handoff` |
| DeepSeek、Bocha、本地检索并行 | 网络分支与本地分支并行；DeepSeek 和 Bocha 是可选 provider，auto 才主备回退。本机及容器实际均为 bocha | `Providers.search`、图的 planner 分叉 |
| 按用户与 Thread 隔离 | 档案/偏好归账号 subject；会话与语义记忆还受工作空间及 Thread 约束 | `Runtime.context`、隔离回归 |
| 报告生成前逐结论核验 | writer 先生成结构化草稿，再 validator 核验/修订/过滤，最后发布报告 | `ResearchGraph.validator` |
| 引用支持率 100%、覆盖率 97.33% | 本次不采用；替换为 99.66% 与 100%，保留门禁失败 | 本次 research-quality JSON |
| 4633 请求、387 完成、46.41 秒恢复 | 历史数据；本次替换为 4576、386、53.50 秒 | 本次 components JSON |

这些核对确认系统能力，不自动证明个人“独立主导”、商业收益或生产使用规模。

## 实时联网验收

完整浏览器测试使用当前 Bocha 搜索、真实模型、真实嵌入与日常单机企业服务。三份报告均产生可读取网页正文来源，PostgreSQL 场景还验证本地资料引用、取消后恢复与 SSE 重放。资料生命周期测试另行验证 ClamAV/索引、检索、删除后不可检索和 viewer 角色拒绝创建研究。脚本成功退出并清理临时账号和成员关系。

三份报告只使用本次生成的文件评分，已复制到 `.cache/audit-20260911/live-browser/`；之前的报告另存 `previous-live-browser/`，不混入本次统计。图外评分 3/3 通过，与固定证据 30 题分别报告。评分日志中的 `seconds` 仅为评分耗时，不是网页研究的端到端耗时；浏览器三题分别约 2.8、3.7、3.0 分钟。

## 质量失败解释

`negation-2` 的证据只说明“甲组 19 人全部完成，乙组 21 人全部未完成”。最终报告既把“完成”解释为“成功”，又在后文表示二者对应关系未被证据确认。图外评分将第一条判为不支持，8 条中支持 7 条，因此该题支持率 87.5%。人数与主体未颠倒，覆盖率仍为 100%，但关键题失败即使总通过率 96.67% 也触发门禁失败。

这说明最终报告的模型核验存在漏检或判定不一致；不能将“有引用”当成“引用必然支持”。本次未更改题目、阈值或实现，也未用重试覆盖失败。局部原始报告与逐条判定留在 `.cache/audit-20260911/research-*/negation-2/review.json`，不向公开结果复制完整正文。

## 可直接采用的四条

- **可审计研究编排：** 基于 LangGraph 与 `ResearchState` 实现路由、对话、规划、网页/本地调研、证据裁决、分析补搜、报告撰写和引用核验共 **10 个同进程职责角色**；通过工具白名单、结构化交接与 SSE 事件记录执行节点、允许工具及交接产物类型，使研究执行过程可追踪、可审计。
- **混合检索与排序优化：** 并行编排网络调研与 Milvus 本地资料检索，支持 DeepSeek/Bocha 搜索适配及自动主备回退，引入 BM25 + 向量融合、重复片段抑制和查询缓存；在 **14 份冻结资料、16 个标注问题**的真实嵌入回归集上，Recall@5 从 **96.88% 提升至 100%**，nDCG@5 从 **94.46% 提升至 96.76%**，本次两轮复测质量结果一致。
- **路由隔离与质量门禁：** 通过 LLM 语义路由区分普通对话、上下文追问和外部事实研究，支持 Quick/Deep 手动模式；将档案偏好按账号私有保存，会话与报告语义记忆按工作空间及 Thread 约束，在最终报告输出前执行逐结论引用、主体、否定和日期范围核验。离线路由回归 **7/7**；修复术语边界后，2026-09-12 固定证据真实模型完整评测 **30/30 全部通过**，图外模型评分的引用支持率 **100%**、应答覆盖率 **100%**，固定研究质量门禁通过。
- **异步任务与故障恢复：** 以 PostgreSQL 为任务权威状态源，结合 Redis/ARQ、请求幂等、尝试版本隔离、心跳恢复和死信队列支撑长任务执行，使用 Docker Compose 与 OpenTelemetry 完成单机交付和链路观测；在可控模型/搜索替身下，**5 客户端、300 秒**业务负载完成 **4576 次请求、386/386 个接受任务**，非预期失败及重复终态均为 **0**，单次 Worker 故障演练 **53.50 秒**恢复。

## 本次复现入口

原始结果根目录为 `.cache/audit-20260911/`，每轮检索使用独立目录与临时向量集合。

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=$env:TEMP/dr-audit-20260911-01
.\.venv\Scripts\ruff.exe check backend tests scripts main.py
npm --prefix frontend test
npm --prefix frontend run build
.\.venv\Scripts\python.exe scripts/evaluate.py --verify --output .cache/audit-20260911/offline/baseline.json
.\.venv\Scripts\python.exe scripts/evaluate_answers.py --output .cache/audit-20260911/answer-quality.json
.\.venv\Scripts\python.exe scripts/evaluate_research.py --output .cache/audit-20260911/research-quality.json
.\.venv\Scripts\python.exe scripts/verify_components.py --load --drill
.\.venv\Scripts\python.exe scripts/smoke-enterprise.py
# 两轮分别使用 /audit/retrieval-1/result.json 和 /audit/retrieval-2/result.json
docker compose run --rm --no-deps -e PYTHONPATH=/workspace -v E:/project/DeepResearch:/workspace:ro -v E:/project/DeepResearch/.cache/audit-20260911:/audit backend python /workspace/scripts/evaluate_retrieval.py --corpus /workspace/eval/local_retrieval_corpus.json --cases /workspace/eval/local_retrieval_cases.json --output /audit/retrieval-1/result.json
$env:E2E_LIVE='1'
$env:E2E_DOCUMENTS='1'
node scripts/e2e-enterprise.mjs
.\.venv\Scripts\python.exe scripts/evaluate_research.py --live --cases eval/live_research_cases.json --external-results .cache/audit-20260911/live-browser --output .cache/audit-20260911/live-quality.json
```

复跑时改用新的输出目录与 pytest 临时目录，避免把旧文件当成本轮结果。
