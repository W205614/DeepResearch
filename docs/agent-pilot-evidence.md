# Agent 项目试用证据：盲评、成本与放行

本流程用于验证 DeepResearch 是否帮使用者完成有来源的研究任务。固定合成题、模型辅助评分、短时负载和单机恢复各有价值，但不能替代真实任务的人工评分或业务收益对照。现有报告审核是**人机协作功能**；它不自动证明评审人核实了事实。

## 1. 先冻结任务与比较条件

从 [`eval/pilot_tasks.template.json`](../eval/pilot_tasks.template.json) 复制一份到 `.cache/pilot/<轮次>/tasks.json`，由试用负责人填写真实、已获准使用的任务。每题需有稳定 `id`（仅字母、数字、连字符或下划线）、原始 `question`、可判定的 `success_criteria` 和至少一份冻结的 `source_files`（TXT、Markdown、PDF 或静态图片）。网页资料先保存为获准分享的静态快照，供评审人独立核对。不要把内部资料、答案或个人信息提交到 Git。记录题集版本、来源范围、评审人、执行者、模型配置和提交 SHA；在拿到结果前冻结题目与评判标准。**仓库模板没有真实用户任务，不能被当作已完成试用。**

任务条目的字段形状如下；题目和文件路径仅作说明：

```json
{"id": "task-001", "question": "已批准的真实研究问题", "success_criteria": "可核对的完成标准", "source_files": [".cache/pilot/round-1/source-001.md"]}
```

对同一题分别执行人工基线与 Agent 辅助流程。开始和结束计时的定义一致，均包含阅读资料、核查与交付，不把 Agent 的审核时间排除在外。人工和 Agent 使用相同的准许资料范围；记录超时、失败和放弃，不能只保留成功答案。记录 Agent 实际账单成本及币种；拿不到账单时填 `null`，Token 数只作为用量而非金额。

把两种答案存为 UTF-8 Markdown 文件，依据 [`eval/pilot_submissions.template.json`](../eval/pilot_submissions.template.json) 填写：

```json
{
  "submissions": [
    {"task_id": "task-001", "arm": "manual", "file": ".cache/pilot/round-1/manual-001.md", "elapsed_seconds": 600, "model_cost_amount": 0, "currency": "CNY"},
    {"task_id": "task-001", "arm": "agent", "file": ".cache/pilot/round-1/agent-001.md", "elapsed_seconds": 420, "model_cost_amount": null, "currency": null, "run_id": "实际任务ID"}
  ]
}
```

上面只是**字段示例**，数值不是测量结果。每道题都需要两份提交。答案正文应去掉能暴露执行方法的表述，但不能改写事实和引用；如果评审人仍能辨认方法，需在最终记录中标注盲评限制。

## 2. 盲评并在全部评分后揭盲

```powershell
.\.venv\Scripts\python.exe scripts/pilot_evidence.py prepare `
  --tasks .cache/pilot/round-1/tasks.json `
  --submissions .cache/pilot/round-1/submissions.json `
  --review-dir .cache/pilot/round-1/reviewer `
  --private-dir .cache/pilot/round-1/private
```

仅将 `reviewer` 目录交给评审人；`private/mapping.json` 保存人工/Agent 映射，不能提前给评审人。评审人打开 `answers/<answer_code>.md`、对应的 `sources/<task_id>/` 和 `review.csv`，按来源原文核对，填写匿名 `reviewer_id`、可用结论数、事实错误数、缺失要求数、需编辑处数、`accepted`（0 或 1）以及驳回原因。多位评审人可为每份答案各复制一行；每位评审人须评完每份答案。先保存并冻结 `review.csv`，然后运行：

评审口径须在揭盲前统一：可用结论应与问题相关且能在冻结资料中定位；事实错误包括主体、数字、否定、范围或引用支持错误；缺失要求按任务的完成标准计数；编辑处只算影响结论或可交付性的实质修改。`accepted=1` 表示评审人愿意按事先约定的用途接收该版本，不等于正式发布审批。

```powershell
.\.venv\Scripts\python.exe scripts/pilot_evidence.py summarize `
  --review-dir .cache/pilot/round-1/reviewer `
  --private-dir .cache/pilot/round-1/private `
  --output .cache/pilot/round-1/private/summary.json
```

输出每题双方法评分与配对均值差。样本小或没有实际参与者时，不做总体准确率、显著性或业务收益宣称。保留失败和评审分歧；对比结果必须连同题集摘要、提交 SHA、工作区是否干净、评审人数、任务来源和环境条件报告。工作区有未提交代码时，不能把 HEAD 单独称为该轮确切代码版本。

## 3. 从现有系统读取任务元数据

本地 SQLite 可直接只读查询（仅适用于使用 SQLite 的本地状态文件）：

```powershell
.\.venv\Scripts\python.exe scripts/pilot_evidence.py telemetry `
  --sqlite .cache/pilot/source.sqlite `
  --workspace-id <工作空间ID> `
  --output .cache/pilot/round-1/private/telemetry.json

```

正式 Compose 的 PostgreSQL 位于内部网络。在服务已启动、`.cache` 目录已创建时运行一次性 Agent 容器；脚本只读挂载，数据库口令来自容器已有的 `DATABASE_URL` 环境变量，结果留在主机被忽略的 `.cache`：

```powershell
docker compose run --rm --no-deps `
  -v "${PWD}/scripts/pilot_evidence.py:/app/scripts/pilot_evidence.py:ro" `
  -v "${PWD}/.cache:/app/.cache" `
  agent python /app/scripts/pilot_evidence.py telemetry --postgres-env DATABASE_URL `
  --workspace-id <工作空间ID> `
  --output /app/.cache/pilot/round-1/private/telemetry.json
```

`telemetry` 仅读取指定工作空间最近 100 条任务，可用 `--run-id` 缩小到指定任务；按任务给出终态、创建至更新的耗时（**含排队，不等于模型时延**）、补搜轮数、失败节点、节点累计时长、Token/调用次数与审核状态和原因。报告正文、来源内容和问题原文不会进入此摘要。审核原因仍可能包含敏感内容，输出只留在 `.cache`；不要提交或外传。Token 用量不直接换算金额，账单成本由试用记录单独提供。按任务 ID 合并盲评和遥测后，才能判断哪些失败或成本值得改代码。

仅在同题对照显示持续的准确性、用时或费用瓶颈时，才调整检索、上下文和模型调用；单凭图文清单不增加 Agent 角色或新的观测平台。

## 4. 回归与放行

原有 30 题含 `compare-1`，不得改题或降低原门槛。新增的 [`research_quality_extensions.json`](../eval/research_quality_extensions.json) 是独立合成回归集，涵盖否定例外、版本冲突、证据不足和来源冲突；Answer Quality Gate 将原 30 题与新增 4 题分别执行，两套门槛独立判定。单独复测命令：

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_research.py `
  --cases eval/research_quality_extensions.json `
  --output .cache/eval/research-quality-extensions.json
```

该命令调用真实模型，会产生成本；仅在批准的模型配置下运行。多轮上下文及资料热更新属于状态化场景，不用上述单题脚本冒充覆盖：继续运行 `tests/test_research.py` 的会话连续性测试及 `tests/test_reranker_hot_update.py` 的资料版本切换测试。固定题和状态化测试均需注明执行日期、提交、配置、失败样本。

正式邀请试用者前，按 [`local-pilot-operations.md`](local-pilot-operations.md) 记录：未参与调优的人工盲评、真实告警收件、机外备份与异机恢复、业务及技术值守人。缺任一项就维持“单机演示、受控试用准备”的表述。代码和离线工具无法代替真实评审人、外部告警接收方或另一台恢复主机。

## 本轮工具与合成回归记录（2026-09-28）

- 新增工具聚焦本地盲评、揭盲对照和数据库只读遥测；无公共 API、迁移或在线模型调用变更。工具测试 3/3、Ruff 全仓检查通过。
- 新增 4 道独立合成回归题在一次真实模型研究图运行中 4/4 通过，图外同模型评分的引用支持与应答覆盖均为 1.0；固定题 SHA-256 为 `8e81a432a18f5360e169266d9ff584b30d43b897e34bad5bffc023aab4f50f50`。运行时业务源码 HEAD 为 `62c163cbb32ea5c67d92217a98a11a4e6a0b089c`，新增题集当时尚未提交；原 30 题没有在本轮重跑。
- 会话连续性定向测试 3/3，资料热更新定向文件测试 14/14。真实试用题、独立人工评分、业务收益、告警收件和机外恢复均未执行，不能从合成结果推导。
