# 面试项目收尾验收

验收日期：2026-09-10。基线为 `8992eea`，环境为 Windows / Docker Desktop、单机 Compose。公开统计见 [delivery-results.json](../eval/delivery-results.json)。代码修改、构建、测试先完成，再更新本文；GitHub 工作流结果以对应提交的 Actions 页面为准。

## 结果与口径

| 项目 | 实际结果 |
| --- | --- |
| Python 回归 | 108 通过；4 个真实组件测试在普通 pytest 中跳过，另在隔离 Compose 全部通过 |
| 前端 | Vitest 4 通过，生产构建通过 |
| 独立回答门禁 | 7 个生成题及 3 个固定错误反例，共 10 通过 |
| 固定完整研究 | 最终本机 30 题全部通过；引用支持率 100%，应回答问题覆盖率 100%；此前 29/30 记录保留 |
| 实时联网 | PostgreSQL RLS、Redis 持久化、OIDC 共 3 题通过浏览器执行和独立评分 |
| 业务负载 | 5 客户端、300 秒，4560 请求，非预期失败 0；379 个接受任务全部完成，无重复终态 |
| 延迟 | 控制/读取 P95 243.71 ms；研究完成 3160.28 ms；索引 1752.44 ms；排队 1376.58 ms |
| 故障恢复 | 强制终止 Worker 后 52.44 秒完成恢复，低于 45 秒心跳窗口 + 10 秒扫描 + 30 秒；Redis 投递恢复、Milvus 就绪恢复通过 |
| 备份恢复 | 隔离恢复 11 个卷、两个 SQL 备份、两个非空向量集合，耗时 202.05 秒；临时卷已清理 |
| 日常 Compose | 重建和企业冒烟通过；最终无模型浏览器回归 3 通过，3 个付费联网场景复用前述结果 |

固定 30 题由六类各五题组成，来源为仓库内编写的合成证据快照，不是实际行业事实。真实模型执行规划、检索、裁决、分析、补搜、撰写和核验，评分在图外另行调用。独立评分是独立提示和答案标注，不代表独立模型或人工盲评。模型仍有随机性。前轮 `single-1` 过度拒答作为失败记录保留；CI 配置修复期间又定位到整份排除恶意资料导致的过度拒答，修正规划与证据裁决后最终 30/30 通过，没有删除题目或放宽门槛。

引用支持率按最终带引用事实逐条计数；覆盖率按应回答题的预期事实计算，空答失败。固定网页评测未测向量检索；组件测试使用确定性 64 维测试嵌入，浏览器资料场景使用配置的真实嵌入。它们不是同一个检索成绩。业务负载使用可控模型和搜索替身，SSE 连接等待包含在研究耗时中，不计普通接口 P95。额度竞争另由真实 PostgreSQL 测试验证（8 次竞争只允许 2 次，删除研究不返还）；正常负载中的零失败不能解释为无限容量。

## 复现命令

从仓库根目录执行，先 `uv sync --all-groups --frozen`。Windows 可将 `uv run` 换为 `.\.venv\Scripts\python.exe` 调用脚本。

```powershell
uv run pytest -q -p no:cacheprovider --basetemp=$env:TEMP/dr-acceptance
uv run ruff check backend tests scripts main.py
python scripts/verify_components.py --load --drill
uv run python scripts/evaluate_answers.py
uv run python scripts/evaluate_research.py
docker compose up -d --build --wait --wait-timeout 300
uv run python scripts/smoke-enterprise.py
$env:E2E_LIVE='1'
$env:E2E_DOCUMENTS='1'
node scripts/e2e-enterprise.mjs
uv run python scripts/evaluate_research.py --live --cases eval/live_research_cases.json --external-results .cache/eval/live-browser --output .cache/eval/live-final.json
```

完整浏览器命令调用真实服务；常规无模型回归不设置 `E2E_LIVE`。脚本创建、清理自己的临时身份，长期运行后刷新管理员令牌再清理。失败 Trace 仅保留本机，不提交。首次 PostgreSQL 联网题只产生资料引用而缺网页引用，加入明确的官方资料入口后重跑通过；其余两题没有重复执行研究，仅独立评分。

```powershell
uv run python scripts/recovery.py --help
.\scripts\backup.ps1
.\scripts\restore.ps1 -BackupPath .cache\backup\interview-final-20260910 -Project dr-restore-interview-final
```

本次升级前已冷备，新增 Alembic `0004_consistency` 保留原数据。历史搜索只保存任务累计次数，因此升级时按任务创建日回填，无法恢复历史逐次调用日期；升级后按 UTC 实际搜索预占日计数。失败外部调用及自动切换备用源各计一次，缓存不计，恢复与删除不重置。不增加 Token 拒绝策略。

## 修复期间遇到的问题

- 资料删除与索引竞态：版本校验、发布锁及持久化清理记录保证旧版本不能重新进入检索；Milvus 写成功但 SQL 未提交也有补偿记录。
- 原质量门禁仅验证关键词：同词反义、数字错属和日期范围错误现在由逐条支持判定拒绝；缺失或重复判定失败关闭。初次完整评测中的无依据原因和建议推动了规划、分析和撰写边界修复。
- Redis 中断后测试 Worker 进程退出：隔离编排补充自动重启，完整负载与故障流程重新执行成功。
- 搜索自动降级原先只计一次：备用源发送前再次原子预占，增加缓存、失败及降级组合回归。
- 首次 GitHub 完整评测遗漏 `LLM_EXTRA_BODY`：本机关闭 thinking，CI 则采用服务默认思考模式。用户提供的中途日志中 21 题已有 8 题失败、4 道关键题失败，单题耗时中位数 164.10 秒，并有 validator 的 ServiceError。该轮已取消，不能记为通过。CI 现显式匹配本机配置，增加 20 分钟任务上限、逐题开始日志和配置哈希；保留告警日志用于区分截断与结构错误。支持率失败与 ServiceError 都必须由新一轮实际验收确认解决，不推定配置修复等于质量通过。
- 浏览器长任务清理时管理员令牌过期：清理前重新取令牌，已清除本次遗留的临时身份。

## 停止边界

本次验收定位为可复现的个人面试项目：单机存储/队列一致性、完整研究与失败边界有证据。没有验证生产高可用、真实用户分布下的 SLA、通用回答准确率或人工标注一致性。达到约定阈值后停止增加功能；保留失败案例和组织级限制用于面试讨论。CI 运行完整 30 题及真实组件故障测试，本地 5 分钟负载与实时联网三题无需每次 push 重复付费执行。
