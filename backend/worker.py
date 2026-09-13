"""ARQ worker entry point. Start with: arq backend.worker.WorkerSettings."""
from arq.connections import RedisSettings
from prometheus_client import start_http_server

from .core.config import Settings
from .core.observability import configure_telemetry, extract_trace_context, tracer
from .core.metrics import initialize_run_terminal_metrics
from .services.runtime import Runtime


async def startup(ctx):
    settings = Settings()
    configure_telemetry(None, settings.otel_exporter_otlp_endpoint, service_name="deepresearch-worker")
    initialize_run_terminal_metrics()
    start_http_server(8001)
    runtime = await Runtime(settings, worker_mode=True).start()
    ctx["runtime"] = runtime


async def shutdown(ctx):
    await ctx["runtime"].close()


async def run_research(ctx, run_id: str, resume: bool = False, trace_context: dict | None = None):
    runtime: Runtime = ctx["runtime"]
    with tracer().start_as_current_span("research.run", context=extract_trace_context(trace_context)):
        await runtime.execute_run(run_id, resume=resume)


async def ingest_document(ctx, document_id: str, trace_context: dict | None = None):
    runtime: Runtime = ctx["runtime"]
    with tracer().start_as_current_span("document.ingest", context=extract_trace_context(trace_context)):
        await runtime.documents.ingest(document_id)


class WorkerSettings:
    functions = [run_research, ingest_document]  # Legacy document jobs drain from the old queue.
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 2
    job_timeout = Settings().max_run_seconds
    max_tries = Settings().max_job_retries + 1
    retry_jobs = False
    keep_result = 0
    allow_abort_jobs = True

    redis_settings = RedisSettings.from_dsn(Settings().redis_url)


class DocumentWorkerSettings:
    # ARQ collects class.__dict__; inherited settings are not discovered.
    functions = [ingest_document]
    queue_name = "arq:documents"
    max_jobs = 1
    on_startup = startup
    on_shutdown = shutdown
    job_timeout = Settings().max_run_seconds
    max_tries = Settings().max_job_retries + 1
    retry_jobs = False
    keep_result = 0
    allow_abort_jobs = True
    redis_settings = RedisSettings.from_dsn(Settings().redis_url)
