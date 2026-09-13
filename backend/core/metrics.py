"""Anonymous Prometheus metrics. Never use user or content values as labels."""
from prometheus_client import Counter, Gauge, Histogram

RUNS = Counter("deepresearch_runs_total", "Research runs by terminal state", ["status"])
RUN_TERMINAL_STATUSES = ("completed", "insufficient", "failed")

def initialize_run_terminal_metrics() -> None:
    """Expose zero-valued terminal counters before the first completed job."""
    for status in RUN_TERMINAL_STATUSES:
        RUNS.labels(status=status)

RUN_SECONDS = Histogram("deepresearch_run_duration_seconds", "Research task duration")
NODE_SECONDS = Histogram("deepresearch_node_duration_seconds", "Graph node duration", ["node"])
FAILURES = Counter("deepresearch_failures_total", "Stable task failure categories", ["category"])
SEARCHES = Counter("deepresearch_searches_total", "Web searches issued")
LLM_CALLS = Counter("deepresearch_llm_calls_total", "LLM calls issued")
TOKENS = Counter("deepresearch_tokens_total", "Model tokens", ["kind"])
QUEUE_DEPTH = Gauge("deepresearch_queue_depth", "Queued research runs")
RAG_RETRIEVAL_SECONDS = Histogram("deepresearch_rag_retrieval_duration_seconds", "Local RAG stage duration", ["stage"])
DLQ_DEPTH = Gauge("deepresearch_dead_letter_depth", "Unrecovered dead-letter research runs")
DLQ_EVENTS = Counter("deepresearch_dead_letter_total", "Dead-letter events", ["action","category"])
ALERT_DELIVERIES = Counter("deepresearch_alert_deliveries_total", "Alertmanager webhook delivery results", ["result"])
ALERT_SUPPRESSED = Counter("deepresearch_alert_suppressed_total", "Repeated Alertmanager events suppressed before delivery")
VISION_CALLS = Counter("deepresearch_vision_calls_total", "Vision calls by outcome", ["outcome"])
VISION_SECONDS = Histogram("deepresearch_vision_duration_seconds", "Vision request duration")
VISION_TOKENS = Counter("deepresearch_vision_tokens_total", "Reported vision token usage", ["kind"])
RETRIEVAL_OUTCOMES = Counter("deepresearch_retrieval_outcomes_total", "Retrieval outcomes", ["source", "outcome"])
FALLBACKS = Counter("deepresearch_fallbacks_total", "Evidence fallback reasons", ["reason"])
for _outcome in ("success", "error"):
    VISION_CALLS.labels(outcome=_outcome)
for _kind in ("prompt", "completion"):
    VISION_TOKENS.labels(kind=_kind)
for _source in ("web", "local"):
    for _outcome in ("ok", "empty", "error", "search_limit_reached", "invalid_records", "vector_unavailable"):
        RETRIEVAL_OUTCOMES.labels(source=_source, outcome=_outcome)
for _reason in ("provider_switch", "empty", "error", "search_limit_reached", "invalid_records", "vector_unavailable", "insufficient_evidence", "image_unreadable"):
    FALLBACKS.labels(reason=_reason)
