# 图片输入、检索兜底与观测

## 使用

研究输入框支持选择、粘贴和拖入图片，可同时附文字，也可只发送图片。
每轮最多 4 张，每张不超过 10 MB；JPEG、PNG、WebP、静态 GIF，最多 4000 万像素。
动画、损坏文件、扩展名与文件签名不符的文件会被拒绝。扫描未通过或不可用时不能发送。

自动模式下，描述图片、读取文字、提取表格等直接使用视觉模型回答，不调用检索；
需要外部核实时进入快速或深度研究。手动选择研究模式优先。
看图回答会标注未经外部核实；研究中图片来源仅证明图中显示的内容，不证明图中说法真实。
原图可通过任务缩略图、来源卡片打开；访问需要登录并具备任务所属工作空间的权限。

`VISION_MODEL_ID` 默认 `deepseek-v4-flash-vision-exp`，复用 `LLM_BASE_URL` 和 `LLM_API_KEY`。
Compose 将此变量同时传给 API 与 Worker。`LLM_EXTRA_BODY` 也适用于视觉请求。
2026-09-13 的实际联调中，此名称返回的模型名为 `deepseek-flash`；接口别名不代表固定模型版本。
可运行 `uv run python scripts/verify_vision.py` 复测。该命令会调用真实接口并消耗额度，
仅发送生成的非敏感图片，结果保存在 `.cache/vision-smoke/result.json`。

## 接口与存储

- `POST /api/attachments`：multipart 单图片上传，校验、解码和扫描成功返回 201 与附件元信息。
- `GET /api/attachments/{id}`：鉴权读取原图，响应禁止缓存。
- `DELETE /api/attachments/{id}`：删除本人尚未发送的附件；已发送图片随会话删除。
- `POST /api/research/runs` 增加可选 `attachment_ids`；其顺序也参与幂等校验。
  不带附件的旧请求保持兼容；纯图片请求自动使用“描述图片并提取关键信息”。
- 任务响应增加 `attachments`；图片证据 `kind=attachment`，包含 `attachment_id`。
  `validation.reasons` 和 `retrieval_outcomes` 提供结构化结果；SSE 增加视觉和检索状态事件。

附件不自动建立向量索引，也不进入共享资料库。上传阶段按上传者隔离，绑定任务后按任务权限读取。
原图复用对象存储，扫描前在 quarantine 区域；任务绑定在数据库事务中完成。
迁移 `0005_image_attachments` 增加附件元数据及视觉结果表；SQLite 测试环境使用相同结构。
部署顺序为构建、`docker compose run --rm --no-deps migrate`、更新 API/Worker/Web。

检查点只包含附件编号和解析结果，不包含 Base64。已完成的视觉解析持久化后可在恢复时复用。
如果外部调用成功但解析结果尚未持久化即发生崩溃，恢复可能重发调用；不承诺外部调用恰好一次。
未绑定附件超过 24 小时会在启动或每 5 分钟的清理中删除；绑定附件在删除会话时标记清理。
对象存储清理失败保留删除标记，后续清理重试。历史纯文本任务和检查点按无附件处理。

## 兜底语义

| 场景 | 结果 |
|---|---|
| 正常检索无结果或证据未通过校验 | 本轮仍可检索且模式允许时补搜，最终 `insufficient` 并引导补充资料 |
| 部分来源失败，但其他证据足够 | 保留有依据的回答，显示服务降级原因 |
| 所有实际尝试的来源均故障，且无可读图片依据 | `failed`，可重试；重试重启检索图，复用已保存图片解析，保留本轮已执行的检索次数 |
| 本轮检索达到上限 | 停止补搜，按现有证据输出结果；可继续发起研究，不限制每日查询或 Token 用量 |
| 图片看不清 | 明确说明并请求清晰图片，不补造数字与文字 |
| 视觉接口故障 | 任务失败，可恢复；不忽略图片继续回答 |

备用搜索只遵循已有 `WEB_SEARCH_PROVIDER=auto` 和备用凭据配置；正常空结果不隐式切换供应商。
不会使用模型常识填充未经证实的事实报告。

## Grafana 与 LangSmith

本次保留 OTel → Tempo/Loki 与 Prometheus → Grafana 链路，不启用 LangSmith 数据上报。
LangGraph 的依赖中出现 langsmith 包不等于接入了 LangSmith 服务。
LangSmith 适合逐次模型输入输出、调用过程和质量反馈调试；现有本机运行监控已覆盖服务层，
替换没有必要。Grafana Cloud Agent Observability 属于另外的云端产品，并非当前本地 Grafana 的内置能力。

- `deepresearch_vision_calls_total{outcome}`、`deepresearch_vision_duration_seconds`：图片理解调用与耗时。
- `deepresearch_vision_tokens_total{kind}`：供应商实际报告的 token 数，同时纳入任务总用量；缺失值不估算成本。
- `deepresearch_retrieval_outcomes_total{source,outcome}`：成功、空结果、故障、本轮检索达到上限、无效字段、向量检索降级。
- `deepresearch_fallbacks_total{reason}`：备用切换及最终结果的兜底原因。

仪表盘新增六个面板；告警在 10 分钟内超过 5 次视觉故障并持续 2 分钟时触发。
日志记录任务关联、返回模型名和固定类别，不记录图片、Base64、完整提示词、密钥。
指标不使用用户、附件名或任务编号作为标签。

参考：[DeepSeek 模型别名公告](https://api-docs.deepseek.com/news/news260910/)、
[LangSmith 调试概念](https://docs.langchain.com/langsmith/observability-concepts)、
[Grafana Cloud Agent Observability](https://grafana.com/docs/grafana-cloud/observe-and-act/agent-observability/introduction/)。

每日搜索配额已移除，API 余额不足与限流分别提示。后续文档解析与检索加固见 [边界处理说明](retrieval-document-hardening.md)。
