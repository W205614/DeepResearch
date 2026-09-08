# 评测指标
检索质量用带 relevant_documents 标注的冻结问题集计算 Recall@K、Precision@K、nDCG@K 和 MRR。Recall 衡量相关资料是否被找回，Precision 衡量 Top-K 中相关资料比例，不能把来源数量称为召回率。最终回答正确率需要独立标注答案事实、完整性与引用支撑。
