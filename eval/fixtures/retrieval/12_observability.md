# 运行与可观测性
FastAPI 通过 SSE 输出节点和 Agent 交接事件。每次本地检索记录缓存、BM25、查询嵌入、向量搜索、融合和总耗时。界面不流式返回模型 token，所以当前系统没有可报告的真实 TTFT 指标。Docker Compose 启动 API、前端、向量库和身份服务。
