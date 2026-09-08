"""Anonymous Prometheus metrics. Never use user or content values as labels."""
from prometheus_client import Counter, Gauge, Histogram

RUNS = Counter("deepresearch_runs_total", "Research runs by terminal state", ["status"])
RUN_SECONDS = Histogram("deepresearch_run_duration_seconds", "Research task duration")
NODE_SECONDS = Histogram("deepresearch_node_duration_seconds", "Graph node duration", ["node"])
FAILURES = Counter("deepresearch_failures_total", "Stable task failure categories", ["category"])
SEARCHES = Counter("deepresearch_searches_total", "Web searches issued")
LLM_CALLS = Counter("deepresearch_llm_calls_total", "LLM calls issued")
TOKENS = Counter("deepresearch_tokens_total", "Model tokens", ["kind"])
QUEUE_DEPTH = Gauge("deepresearch_queue_depth", "Queued research runs")
RAG_RETRIEVAL_SECONDS = Histogram("deepresearch_rag_retrieval_duration_seconds", "Local RAG stage duration", ["stage"])