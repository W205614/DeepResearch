"""ARQ worker entry point. Start with: arq backend.worker.WorkerSettings."""
from arq.connections import RedisSettings
from prometheus_client import start_http_server

from .core.config import Settings
from .services.runtime import Runtime


async def startup(ctx):
    settings = Settings()
    start_http_server(8001)
    runtime = await Runtime(settings, worker_mode=True).start()
    ctx["runtime"] = runtime


async def shutdown(ctx):
    await ctx["runtime"].close()


async def run_research(ctx, run_id: str, resume: bool = False):
    runtime: Runtime = ctx["runtime"]
    await runtime.execute_run(run_id, resume=resume)


class WorkerSettings:
    functions = [run_research]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 2

    redis_settings = RedisSettings.from_dsn(Settings().redis_url)
