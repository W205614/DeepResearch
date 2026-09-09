"""Privacy-safe task logging and OpenTelemetry setup."""
import logging
import json

class JsonFormatter(logging.Formatter):
    def format(self, record):
        from opentelemetry import trace
        context = trace.get_current_span().get_span_context()
        return json.dumps({"timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"), "level": record.levelname, "logger": record.name, "message": record.getMessage(), "trace_id": f"{context.trace_id:032x}" if context and context.is_valid else None}, ensure_ascii=False)


LOGGER_NAME = "deepresearch.task"
_otlp_log_handler: logging.Handler | None = None


def configure_task_logger(level: str) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    if _otlp_log_handler is not None and _otlp_log_handler not in logger.handlers:
        logger.addHandler(_otlp_log_handler)
    logger.propagate = False
    if not getattr(logger, "_deepresearch_observability_logged", False):
        logger.info("task logger initialized")
        logger._deepresearch_observability_logged = True
    return logger


def get_task_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def run_label(run_id: str) -> str:
    return str(run_id)[:8]


def error_category(error: Exception) -> str:
    text = str(error)
    if "超时" in text or "网络" in text or "连接" in text:
        return "network"
    if "内网" in text or "保留地址" in text or "DNS" in text:
        return "source_blocked"
    if "正文" in text or "登录" in text or "付费" in text:
        return "source_unreadable"
    if "API 密钥" in text or "额度" in text:
        return "provider_configuration"
    if "模型" in text or "JSON" in text:
        return "model_response"
    if "时限" in text:
        return "timeout"
    return type(error).__name__.lower()


def configure_telemetry(app=None, endpoint: str = "", *, service_name: str = "deepresearch-api") -> None:
    """Export safe service spans and task logs without prompts, URLs, or user data."""
    global _otlp_log_handler
    from opentelemetry import trace
    from opentelemetry.trace import ProxyTracerProvider
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service_name})
    if isinstance(trace.get_tracer_provider(), ProxyTracerProvider):
        provider = TracerProvider(resource=resource)
        if endpoint:
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
        trace.set_tracer_provider(provider)

    if endpoint and _otlp_log_handler is None:
        from opentelemetry import _logs as otel_logs
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        log_provider = LoggerProvider(resource=resource)
        log_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint, insecure=True)))
        otel_logs.set_logger_provider(log_provider)
        _otlp_log_handler = LoggingHandler(level=logging.NOTSET, logger_provider=log_provider)
        _otlp_log_handler.setFormatter(JsonFormatter())

    if app is not None:
        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,livez,readyz,metrics")

def tracer():
    from opentelemetry import trace
    return trace.get_tracer("deepresearch")


def inject_trace_context() -> dict[str, str]:
    from opentelemetry.propagate import inject
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def extract_trace_context(carrier: dict | None):
    from opentelemetry.propagate import extract
    return extract(carrier or {})
