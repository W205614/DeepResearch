"""ARQ worker entry point: python -m backend.worker research|documents."""
import argparse
import asyncio
import signal

from arq.worker import create_worker
from prometheus_client import Gauge, start_http_server

from .core.config import Settings
from .core.queue import redis_settings
from .core.observability import configure_telemetry, extract_trace_context, tracer
from .core.metrics import initialize_run_terminal_metrics
from .core.worker_health import drain_marker, mark_draining, write_state
from .services.runtime import Runtime

WORKER_READY = Gauge("deepresearch_worker_ready", "Worker can reach SQL/queue and accept new jobs")
WORKER_DRAINING = Gauge("deepresearch_worker_draining", "Worker is draining and not accepting new jobs")


async def health_loop(ctx):
    runtime = ctx["runtime"]
    path = runtime.settings.worker_health_file
    while True:
        if drain_marker(path).exists():
            runtime.stopping = True
            if "worker_instance" in ctx:
                ctx["worker_instance"].allow_pick_jobs = False
        database_ready = queue_ready = False
        try:
            async with asyncio.timeout(1):
                await runtime.ready()
                database_ready = True
        except Exception:
            database_ready = False
        try:
            async with asyncio.timeout(1):
                queue_ready = bool(await ctx["redis"].ping())
        except Exception:
            queue_ready = False
        WORKER_READY.set(int(database_ready and queue_ready and not runtime.stopping))
        WORKER_DRAINING.set(int(runtime.stopping))
        write_state(path, ctx["worker_role"], database_ready=database_ready, queue_ready=queue_ready,
                    draining=runtime.stopping)
        await asyncio.sleep(5)


async def startup(ctx, *, role="research"):
    settings = Settings()
    ctx["worker_role"] = role
    drain_marker(settings.worker_health_file).unlink(missing_ok=True)
    write_state(settings.worker_health_file, role)
    configure_telemetry(None, settings.otel_exporter_otlp_endpoint, service_name="deepresearch-worker")
    initialize_run_terminal_metrics()
    start_http_server(8001)
    runtime = await Runtime(settings, worker_mode=True).start()
    ctx["runtime"] = runtime
    ctx["health_task"] = asyncio.create_task(health_loop(ctx), name="worker-health")


async def document_startup(ctx):
    await startup(ctx, role="documents")


async def shutdown(ctx):
    runtime = ctx["runtime"]
    runtime.stopping = True
    WORKER_READY.set(0)
    WORKER_DRAINING.set(1)
    ctx["health_task"].cancel()
    await asyncio.gather(ctx["health_task"], return_exceptions=True)
    try:
        write_state(runtime.settings.worker_health_file, ctx["worker_role"], draining=True, stopped=True)
    finally:
        await runtime.close()


async def run_research(ctx, run_id: str, resume: bool = False, trace_context: dict | None = None,
                       expected_attempt: int | None = None):
    runtime: Runtime = ctx["runtime"]
    if runtime.stopping or drain_marker(runtime.settings.worker_health_file).exists():
        return
    with tracer().start_as_current_span("research.run", context=extract_trace_context(trace_context)):
        await runtime.execute_run(run_id, resume=resume, expected_attempt=expected_attempt)


async def ingest_document(ctx, document_id: str, trace_context: dict | None = None):
    runtime: Runtime = ctx["runtime"]
    if runtime.stopping or drain_marker(runtime.settings.worker_health_file).exists():
        return
    with tracer().start_as_current_span("document.ingest", context=extract_trace_context(trace_context)):
        await runtime.documents.ingest(document_id)


class WorkerSettings:
    functions = [run_research, ingest_document]  # Legacy document jobs drain from the old queue.
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = Settings().research_worker_max_jobs
    job_completion_wait = Settings().worker_drain_seconds
    health_check_interval = 5
    job_timeout = Settings().max_run_seconds
    max_tries = Settings().max_job_retries + 1
    retry_jobs = False
    keep_result = 0
    allow_abort_jobs = True

    redis_settings = redis_settings(Settings())


class DocumentWorkerSettings:
    # ARQ collects class.__dict__; inherited settings are not discovered.
    functions = [ingest_document]
    queue_name = "arq:documents"
    max_jobs = 1
    on_startup = document_startup
    on_shutdown = shutdown
    job_timeout = Settings().max_run_seconds
    max_tries = Settings().max_job_retries + 1
    retry_jobs = False
    keep_result = 0
    allow_abort_jobs = True
    job_completion_wait = Settings().worker_drain_seconds
    health_check_interval = 5
    redis_settings = redis_settings(Settings())


def main():
    parser = argparse.ArgumentParser(description="Run a draining-aware ARQ worker")
    parser.add_argument("role", choices=("research", "documents"))
    args = parser.parse_args()
    worker = create_worker(WorkerSettings if args.role == "research" else DocumentWorkerSettings)
    worker.ctx["worker_instance"] = worker

    def begin_draining(signum):
        runtime = worker.ctx.get("runtime")
        if runtime is not None:
            runtime.stopping = True
            mark_draining(runtime.settings.worker_health_file)
        worker.handle_sig_wait_for_completion(signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            worker.loop.add_signal_handler(signum, begin_draining, signum)
        except NotImplementedError:
            # ARQ's native Windows development runner has the same signal limitation.
            pass
    worker.run()


if __name__ == "__main__":
    main()
