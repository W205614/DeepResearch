# 扩展资料与问题集

此目录用于资料版本、检索和有据回答的扩展验收，不代替独立用户测试。

当前[研究证据状态](research-evidence-status.json)为混合证据：35题历史语义结果离线兼容、18题完成新协议组件补验、1题定向通过，另6题保留待验。用户确认停止继续本地付费，采用功能、恢复、完整混合运行及原有最终CI作为本次交付门槛；没有删题、改阈值或宣称60题全部通过。已知内容缺陷及未完成步骤见[交付记录](../../docs/lifecycle-delivery-20261020.md)。

- `corpus.json`：30 份当前资料；20 份自行编写的合成制度与 10 份冻结的公开文档快照。另保留 6 份旧制度版本，见 `version_cases.json`。实际入库片段数由评测输出记录。
- `research_cases.json`：50 个固定问题；`development.json` 与 `holdout.json` 分别记录 30 个开发问题和 20 个保留问题。研发迭代已查看失败结果，因此后续结果应称固定集回归，不能再称未见过的盲测。
- `retrieval_cases.json`：对应 50 个问题的文档级相关性标注。Recall、nDCG 与 MRR 是文档命中指标，不等于逐片段证据准确率。
- `public_sample.json`：另 10 个不同问题，取自同一组公开资料；问题与前述 50 题不同，资料来源并不独立。
- `provenance.json`：来源、上游提交与文件 SHA256。公开快照保留 Kubernetes 文档归属，按 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 使用。原文取自 `kubernetes/website`，删除了前置信息和 HTML 注释，添加了来源与许可说明，未执行文中的命令。
- `evidence-includes/`：固定研究输入所需的同一上游提交的术语定义原文与依赖 SHA256。`research_cases.json`（及对应开发／保留分区）展开 `glossary_definition` 内容宏，避免把未提供的定义当作模型应当知道的事实；`documents/`、检索标注及 `public_sample.json` 继续使用原始快照，字节未因此修改。检索集与研究输入的资料处理方式不同，评测报告分别记录各自指纹，不能混称完全相同的输入。

合成制度用于构造相近内容、否定词、数值和版本边界，不代表真实企业制度。公开文档具有原文依据，但没有独立人工盲评；使用配置模型进行回答与核验，存在同模型偏差。

`sha256` 校验已冻结文件的实际字节，`normalized_text_sha256` 保留统一为LF后的内容哈希。维护机器最初生成的文档为CRLF，原始上游依赖为LF；Git属性保留这些已测字节，避免跨平台检出转换。修正来源清单的哈希定义没有修改48份冻结输入的字节或模型输入；生成器今后确定写入UTF-8/LF，再从落盘字节计算哈希。

生成器 `scripts/build_lifecycle_corpus.py` 会联网刷新并重新冻结上游版本。普通回归直接使用已提交文件，不运行生成器，以免无意改变评测集。

以下研究评测使用配置的真实模型，会产生供应商调用费用。固定资料输入并不表示模型响应也被模拟；先限定问题范围，保存报告和调用记录，修复时只补受影响的核验，不反复生成同一批报告。真实嵌入与重排评测也按所配置服务计费。

```powershell
uv run python scripts/evaluate_research.py --cases eval/lifecycle/research_cases.json --output .cache/eval/lifecycle-research.json
uv run python scripts/evaluate_research.py --cases eval/lifecycle/public_sample.json --output .cache/eval/lifecycle-public-sample.json
```

检索评测需配置真实嵌入、Milvus 和可选重排服务，使用独立输出目录与集合，不能对日常数据运行破坏性清理。以 `scripts/evaluate_retrieval.py --help` 为准。

`deployment/compose/compose.acceptance.yaml` 的浏览器角色、故障和30分钟混合运行则使用本地确定性模型、嵌入与搜索夹具，关闭重排，不消耗真实模型 API 额度；数据库、索引、队列、身份与权限组件仍真实运行。这些检查证明组件协作与恢复行为，不能作为真实模型回答质量的证据。GitHub 的现有 Answer Quality Gate 属于真实模型评测，推送时会另行调用。
