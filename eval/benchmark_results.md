# 冻结本地检索基准

## 范围

- 语料：`eval/fixtures/retrieval/` 中 14 份内部说明资料，按文件名标注。
- 问题：`eval/local_retrieval_cases.json` 中 16 个中文查询，每个查询人工标注相关资料。
- 嵌入：`text-embedding-3-large`，真实已配置服务；Milvus 运行于 Docker 内网。
- 对照：单向量检索（相同嵌入和 Milvus 索引）与当前 BM25 + 向量融合（向量权重 0.65、BM25 权重 0.35、重复片段抑制）。

这是小型、项目自有的回归集，用于验证实现是否退化，不能外推为通用行业语料成绩，也不是最终生成回答正确率或服务 SLA。

## 2026-09-07 实测结果

| 指标 | 单向量基线 | 混合召回 | 变化 |
| --- | ---: | ---: | ---: |
| Recall@3 | 0.9688 | 0.9688 | 0.00 个百分点 |
| Recall@5 | 0.9688 | 1.0000 | +3.12 个百分点 |
| Precision@5 | 0.2250 | 0.2375 | +1.25 个百分点 |
| nDCG@5 | 0.9446 | 0.9676 | +2.30 个百分点 |
| MRR@10 | 0.9583 | 0.9688 | +1.05 个百分点 |
| 平均冷查询 | 1196.40 ms | 1257.78 ms | +61.38 ms |
| 平均热查询 | 不适用 | 94.87 ms | 查询结果缓存命中率 100% |

混合召回在这个集合上改善了 Top-5 覆盖和排序，但 Top-3 召回没有改善，因此不能声称其在所有数据上都显著提高召回率。

## 成本与时延解释

冷查询两种方法各需要 1 个查询嵌入输入；混合召回额外执行 BM25 与融合，带来约 61 ms 平均冷查询开销。混合热查询命中缓存后不再调用查询嵌入，平均为 94.87 ms。索引建立对这套语料只需一次性嵌入 14 个片段；本次检索评测不调用生成模型。

这里报告的是输入请求计数，不是金额：实际货币成本取决于部署时嵌入服务的定价。端到端“用户请求到首个 token”也未报告，因为当前 Web 界面不流式输出模型 token；完整深度研究还会包含规划、网页访问、证据裁决、分析、写作和核验的模型耗时。

## 复跑

```powershell
# 离线回归，不调用外部嵌入服务
.\\.venv\\Scripts\\python.exe .\\scripts\\evaluate_retrieval.py --demo --corpus .\\eval\\local_retrieval_corpus.json --cases .\\eval\\local_retrieval_cases.json

# 真实嵌入：需从能访问 Milvus 的 Docker 网络执行，或使用 compose.dev.yaml 暴露 19530 端口
.\\.venv\\Scripts\\python.exe .\\scripts\\evaluate_retrieval.py --corpus .\\eval\\local_retrieval_corpus.json --cases .\\eval\\local_retrieval_cases.json
```

要得到“回答准确率”，需要另建问题、参考答案和证据标注集，并由人工或双盲判定答案事实正确性、完整性和引用支撑；本评测不会用检索指标替代它。
