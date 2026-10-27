# CI 付费质量门禁额度核算与累计控制

用户已明确授权：本轮所有成功、失败及重跑合计最多 **515 次新增模型 HTTP**。原历史 **843** 次逐条保留，累计模型/LLM/总 HTTP 上限为 **1358**（原900增加458），输入与输出余额仍为 **9101235 / 379122**；embedding、reranker、search 均未扩容。原授权文件 SHA256 为 `713a5a31b13ac9cd9e898a0a579e9a7a39b4ee9d9e68da20e8f4525da1054113`，语义授权及来源账本随代码冻结。

515 超过已证明必要下界245及更紧通过率下界296，仍不保证足够完成或通过。历史414/429只是原版本实测。本地仅完成离线测试；**真实 GitHub 剩余额度必须由每个 run 开始前的 artifact reconciliation 得出，尚未触发付费 CI，也未把离线515当作实时余额。** 旧57不足的判断属于授权前历史，已不再作为当前阻塞。

## 分阶段必过下界

| 阶段 | 原覆盖范围 | 不假设非关键样本全部成功的必要下界 |
| --- | --- | ---: |
| answers | 7 个真实题目 + 3 个反向构造，共 10 个结果 | 7 次生成 + 3 次反向支持评分 + `forbidden-invention` 至少 1 次支持评分 = **11** |
| research | 30 题，25 positive、5 refusal；其中 15 positive 与 5 refusal 为 critical | 15×8 + 5×2 = **130** |
| stability | `conflict-1`、`compare-3`、`single-5`，各 3 次；`--require-all` | 9×8 = **72** |
| extensions | 4 个 positive，全部 critical | 4×8 = **32** |
| 合计 | 四条原调用与全部题目、断言、阈值均保留 | **245** |

research 的 15 个 critical positive 为 `conflict-1..5`、`negation-1..5`、`injection-1..5`；5 个 refusal 为 `refusal-1..5`。extensions 为 `pilot-negation-exception-1`、`pilot-conflicting-versions-1`、`pilot-partial-evidence-1`、`pilot-conflict-no-resolution-1`。

成功 positive 的 8 次由以下调用构成：

| 必需调用 | 源码依据与 fresh 条件 |
| --- | --- |
| planner | `ResearchGraph.planner` 无结果缓存地调用 `ask('planner')`；评测强制 deep，成功结果检查 planner 节点完成 |
| judge | 首次有固定来源的判定调用 `ask('judge')`；当前 case 隔离数据库与初始状态没有前轮已接受证据 |
| analyst | 事实进入写作前需有分析；首次有效证据没有可复用 `analysis_input_hash`，调用 `ask('analyst')` |
| writer | 有事实的分支调用 `ask('writer')` 组织已有结论；无事实分支不会产生可被 positive 通过判定接受的引用事实 |
| 内部 validator | positive 报告至少保留一条批准结论；纯规则预检不能批准结论，首次程序目标必须经过模型核验。fresh 数据库没有可先验复用的核验缓存 |
| 内部 coverage | 有批准结论且 `Plan.questions` 至少一项时调用 `ask('coverage')` |
| 图外 support | `evaluate_research.evaluate` 只评分最终可见引用结论；positive 通过要求 `bool(claims)`，`assess_support` 对非空结论至少调用一次模型 |
| 图外 coverage | `evaluate_research.evaluate` 无条件调用独立 `quality_coverage` |

报告的非事实标题、局限等使用 `md_text` 转义引用括号，参考来源列表被评测显式排除，不能凭这些位置伪造“存在最终引用结论”来绕开上述 positive 路径。refusal 的保守 2 次只计 planner 和图外 coverage，故意不计可能发生的其他调用。每个 `structured` 在 `demo_mode=False` 下至少有一次供应商 POST；所有这些函数、评测入口、格式化器和预算包装器均绑定于 `eval/ci-budget-source-basis.json` 的已审 AST 指纹。

当前门槛允许一部分非 critical 失败：answers 至少 9/10，research 至少 26/30，同时所有 critical 必须通过。由此 answers 至少 14 次、research 至少 178 次，再加 72 和 32，得到更紧的必要下界 **296**。这个计算尚未利用覆盖均值等进一步约束，因此仍只用于排除明显不足，不能作为足额承诺。

早期粗分解 139 将“全部样本均正确完成”的调用假设与实际允许非 critical 失败的门槛混在一起，未作为预检证明或预算请求继续使用。

## 理论包络与历史实测分开看

单次 `structured` 最多 2 次格式尝试，每次 `post` 最多 3 次 HTTP，适配器包络为 6。图内每任务默认累计上限 80，包含 HTTP 重试；图外支持评分在上下文过大时递归拆分，取保守调用树上界 `2C−1`，`ReportDraft` 至多 8×12=96 条结论；独立覆盖再最多 6 次。因此参数化的宽松包络是：

```text
单个 research 执行 <= 80 + 6×(2×96−1) + 6 = 1232
answers <= (7次draft + 最多7次support + 3次adversarial)×6 = 102
四阶段 <= 102 + (30+9+4)×1232 = 53078
```

这不是实际预测、建议授权量或需要预付的费用。时间、token、错误停止和全局账本常会更早截断；过大上下文的递归父调用也可能在 HTTP 前失败，所以该树包络刻意偏宽。

已逐条核对两份历史请求账本，usage 均完整：

| 历史提交 | answers | research | stability | extensions | HTTP | 输入 token | 输出 token |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `062b076` | 16 | 276 | 97 | 40 | **429** | 458509 | 60842 |
| `c764c90` | 16 | 272 | 86 | 40 | **414** | 440256 | 60036 |

原 900 次累计授权减去两轮 843 次，剩余确为 57；输入、输出累计分别 898765、120878，对应当前余额 9101235、379122。两轮使用的源码、模型、端点哈希和 thinking 配置记录在 JSON 中；当前 Python、核验边界和评测适配器已有变化，不能把历史 414/429 当作新版本确定成本或稳定通过概率。

## 已实施的跨 run 控制

- `ci_budget_ledger.py` 保存历史843条原始请求、来源 run/attempt/SHA、原编号和授权摘要。新请求继续使用844起的累计编号；更换路径、清空账本、策略不一致均不能启动 CI 供应商调用。SQLite事务原子预留，含失败与HTTP重试。
- `reconcile_ci_budget.py` 在付费前只读 GitHub Actions API，逐 run/attempt 核对 job步骤、完成状态、完整原始SQLite+JSON+receipt、run/id/SHA、API artifact SHA256、文件摘要及追加前缀。重复 artifact 幂等合并，分叉并发账本拒绝合并。已开始付费但缺完整 artifact 或未知调用数量时 fail closed。缺 usage 但已有请求编号/预留的行仍按 input_bound/output_bound 和一次 HTTP 计费；有余额且无原有 halt 时可以继续。
- 原401/402/403/429/500/502/503/504 halt保持，不因新run清零。所有新workflow使用同一个全局 concurrency group，`cancel-in-progress:false`；push/PR、不同SHA及失败重跑共用额度。禁止手动重跑授权前的旧workflow代码；此类未知旧run会被后续对账拦截，旧代码本身无法追溯获得新锁。
- 四条原调用、case、flags、断言与阈值保持。每run成功或失败始终封存并上传原SQLite/JSON/remaining receipt（90天）；若对账本身失败则上传失败报告，绝不伪造零消耗完整账本。artifact丢失/过期意味着需要人工恢复可核验原账本，不能当0重新开始。
- 模型开始标记前，等待**同一source SHA**普通CI与Security全部success，最多900秒，整个质量job90分钟。失败或仍pending均0供应商调用明确失败，可稍后同SHA重跑。`checkout HEAD == GITHUB_SHA == Actions run.head_sha`必须同时成立；PR merge SHA与API head不同则拒绝，不能拿分支头检查冒充merge检查。复用质量结论仍受原一次性Java OS直接子提交合同限制，账本恢复不等于质量结论复用。
- 静态必要额度预检只接受已对账的真实余额，缺series/auth/run身份不能把累计1358当作新额度。评测CLI把产品治理cap显式设置为该已对账余额，正常UsageGovernor继续工作；产品默认subject200未改。research每题/repetition本来就是独立DB，阶段>200并非共享subject200；此接线也避免单题图外评分递归碰到不相关默认上限。

离线验证包括：523个并发预留恰有515成功；失败run恢复只剩514；重复合并不重扣；缺账本、缺job明细、不同SHA/run/attempt/digest、过期artifact、分叉账本全部拒绝；known reservation缺usage保守记账；HTTP429 halt跨run保持；同SHA pending/failed先决门禁0调用；新路径不能重置；原四条命令保持。供应商HTTP为0，未push/触发CI。

GitHub API字段依据：[artifact digest与workflow_run](https://docs.github.com/en/rest/actions/artifacts?apiVersion=2022-11-28)、[run attempts与jobs](https://docs.github.com/en/rest/actions/workflow-runs)、[concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)。API不可用、权限不足或库存不完整均保守失败。


The four paid targets now run in the same-source production Dockerfile CPython3.11.18 image. Host Git/reconciliation/sealing stay unpaid. Actual version, upstream distrusted-CA rejection(code28) and valid local TLS must pass with network=none first. Clean HEAD, tracked byte hashes, preflight digest and image ID bind to the reconciled run/attempt/SHA and SQLite ledger; the container independently verifies this before transport-hook installation. Source/rootfs are read-only, UID is non-root, only .cache/eval and bounded tmpfs are writable. GITHUB_TOKEN is never forwarded and checkout credentials are not persisted. The cumulative515 allowance, four stage arguments/cases/thresholds are unchanged. Fresh GitHub quality results remain pending.
