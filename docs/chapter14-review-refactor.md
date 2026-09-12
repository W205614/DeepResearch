# Hello Agents 第十四章对照评审与搜索调度重构

## 结论与范围

本次基于当前工作区源码，对照用户指定的 [Hello Agents 第十四章](https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter14/第十四章%20自动化深度研究智能体.md)。适合借鉴的是服务职责拆分、显式研究计划和搜索结果整理。当前项目已有研究图、引用核验和持久化恢复，无需迁移到另一套 Agent 框架。

重点阅读 `backend/research/graph.py`、`agents.py`、`evidence_policy.py`、`backend/domain/models.py`、`backend/infrastructure/providers.py`、`backend/services/runtime.py`，并核对安全读取、文档检索、前端报告渲染、测试和 CI 配置。这是核心研究链路评审，不是全仓逐行安全审计。

最新已提交的研究质量记录见 [完整复测](research-full-20260912.md)：原始 30 题通过。它替代此前 29/30 的历史结论，但不代表这次重构已重新获得真实模型的 30/30。

## 教程与项目的取舍

| 教程机制 | 当前源码与判断 | 本次处理 |
| --- | --- | --- |
| 规划、总结、报告、搜索分服务（14.5） | `ResearchGraph` 已有职责节点，但查询和候选调度仍内嵌图方法 | 抽出无数据库、无模型调用的 `search_policy.py` |
| TODO 子任务与阶段进度（14.3、14.6） | 已有 `Plan.questions`、节点事件、Agent 交接、SSE 和计划展示 | 保留现有结构，补齐停止补搜的原因 |
| URL 去重与内容截断（14.5.4） | 已有规范 URL、正文读取、内容哈希合并和每来源上下文截断 | 修正轮转选择对重复链接的处理 |
| 文件笔记与搜索缓存（14.4、14.5.4） | 已有运行级搜索缓存、正文 TTL 缓存和图检查点 | 保留数据库作为状态源；不新增平行笔记状态 |
| 工具回调观测（14.3.2） | 已有角色权限、事件、日志和追踪 | 保留脱敏日志，不记录完整工具参数或结果 |
| Markdown 报告展示（14.6） | 前端已有 `DOMPurify`，后端有引用规则和逐条核验 | 保留当前安全与证据边界 |

教程的时间收益属于教程描述，本次未据此宣称性能提升。教程示例缓存未展示过期策略，字符数估算也不能当作精确中文 Token 预算；这些示例不直接移植。

14.6.2 的演示片段存在接口不一致：后端声明 POST，前端 EventSource 发 GET；服务端完成消息是 `progress` 的 `stage=completed`，前端却按 `type=completed` 关闭连接。它们说明教程片段不能直接当作经过端到端验证的实现。项目继续使用现有 API 与事件协议。

## 已实现的改动

### 1. 查询规范化贯穿计划、检索和补搜

原规划只按原字符串去重，反思仅对历史查询执行部分规范化，同批补充查询仍可能留下大小写变体。空白查询也满足原模型列表长度约束。

新增 `normalize_queries`：折叠空白、以 `casefold` 比较、保留第一次出现的写法，同时过滤历史查询和本批重复项。保留标点与搜索操作符，不进行语义改写。规划先规范化再应用 quick 限制，全空时回退原主题；计划事件与实际查询一致。网络和本地检索入口也规范化，兼容旧检查点中的查询。

边界：不做同义句去重；`searched` 仍表示尝试过的查询，失败重试继续依赖运行恢复与提供方缓存，不增加跨用户缓存。

### 2. 候选名额按不同有效链接分配

原实现按搜索结果数组下标轮转。某查询前几项是同一网页的追踪链接时，这些重复项会耗掉轮次，其他查询可能提前占满候选上限。

新增 `select_web_candidates`：每个查询在本轮寻找下一个未选的有效 URL；重复或无效项跳过，结果耗尽后退出轮转。仍保持查询顺序、各查询内部排名、规范 URL 和总候选上限。

固定反例：A 返回 A1、A1 的追踪链接、无效地址、A2；B 返回 B0 至 B3；上限为 4。原逻辑选 A1/B0/B1/B2，新逻辑选 A1/B0/A2/B1。收益是避免候选名额被重复行分布扭曲，不等于已测得最终报告准确率提升。DNS、跳转和正文读取检查仍由原安全读取器执行。

### 3. 补搜停止可解释

保留 `reflection` 事件的 `queries`、`reason` 字段，新增 `stop_reason`：`quick_mode`、`no_gaps`、`round_limit`、`no_search_capacity`、`no_new_queries`。现有界面继续显示中文原因，无需修改前端协议。

同时修正 `auto` 模式的可用性判断，使配置了博查备用密钥时不会仅因缺少 DeepSeek 密钥而直接判断无搜索能力。这里仅判断配置与运行搜索额度；真实网络成功和工作空间配额仍由提供方执行时决定。

## 验证与限制

新增 `tests/test_search_policy.py`，覆盖查询变体、保留操作符、全空规划回退、旧状态入口、查询间共享 URL、无效 URL、名额重分配、补搜停止原因和搜索提供方配置组合。原研究流程测试继续覆盖恢复不重复搜索、取消、预算原子性、来源核验、权限和日志脱敏。

最终代码验证结果：

- 全部后端测试：**131 passed，4 skipped**。4 项为需要 `INTEGRATION=1` 和隔离 Compose 的真实组件测试。
- 新增搜索策略测试：14 项通过，已包含在上述 131 项内。
- `ruff check backend tests scripts main.py`：通过。
- 离线流程基线 `scripts/evaluate.py --verify`：7/7 路由与来源数量目标通过；结果位于 `.cache/chapter14-20260912/baseline.json`，只证明固定替代服务下的流程没有退化。
- `git diff --check`：通过。

复现命令（PowerShell）：

```powershell
.venv/Scripts/python.exe -m pytest -q -o cache_dir=.cache/pytest-cache-chapter14 --basetemp=.cache/pytest-chapter14-final-all
.venv/Scripts/ruff.exe check backend tests scripts main.py
.venv/Scripts/python.exe scripts/evaluate.py --verify --output .cache/chapter14-20260912/baseline.json
```

测试使用隔离临时目录和固定替代服务，不修改业务数据库。初次运行出现原 `.pytest_cache` 写权限警告，最终全量运行指定独立缓存目录后无警告。

随后完成 Docker 重建与交付验证：`docker compose up -d --build --wait --wait-timeout 300` 成功；后端、Worker 和 Web 健康，后端及 Worker 中两个研究代码文件的 SHA-256 与本地一致。`scripts/smoke-enterprise.py` 通过 Web、OIDC、数据库迁移、Redis、后端就绪、Worker、Prometheus、Grafana、Tempo、Loki 与 OTel 检查。

上述 Docker 冒烟不调用真实模型或搜索 API，也不替代前述跳过的 4 项隔离组件测试。尚未执行真实搜索/真实模型重新评测或 Docker 组件故障演练，因此不宣称本轮提升了答案准确率、吞吐或恢复时间。

## 后续优化优先级

1. 建立包含重复链接、相互重叠查询的冻结检索题集，对比有效候选覆盖、正文成功率、搜索调用数和最终结论支持情况，再决定是否改变并发度或候选上限。
2. 为证据上下文增加总预算与截断可见性。当前 `evidence_context` 按来源截断，重要段落可能在截断位置之后；应以保留关键事实和引用完整性为验收条件，而非只追求更少 Token。
3. 如果加入子任务笔记，应让笔记关联原始来源 ID 并存入现有状态源，避免把模型总结再当作新证据。先验证事实保真与成本收益，再增加总结调用。

本轮没有增加 Agent 数量或替换 LangGraph；结构拆分服务于已复现的调度问题。
