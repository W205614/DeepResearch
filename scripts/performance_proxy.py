"""Test-only Agent reverse proxy with deterministic latency/status injection."""
import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(base_url="http://api:8000", timeout=120)
        yield
        await app.state.client.aclose()

    app = FastAPI(lifespan=lifespan)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy(path: str, request: Request):
        params = list(request.query_params.multi_items())
        delay = next((value for key, value in params if key == "perf_delay_ms"), "0")
        status = next((value for key, value in params if key == "perf_status"), "0")
        clean_params = [(key, value) for key, value in params if not key.startswith("perf_")]
        try:
            delay_ms = min(60_000, max(0, int(delay)))
            injected_status = int(status)
        except ValueError:
            return Response(status_code=422, content=b"invalid performance fault")
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        if any(key == "perf_status" for key, _ in params):
            return Response(status_code=injected_status, content=b'{"detail":"injected upstream failure"}',
                            media_type="application/json")
        headers = {name: value for name, value in request.headers.items()
                   if name.lower() not in {"host", "content-length", "transfer-encoding", "connection"}}
        upstream = await request.app.state.client.request(
            request.method, "/" + path, params=clean_params, headers=headers, content=await request.body())
        response_headers = {name: value for name, value in upstream.headers.items()
                            if name.lower() in {"content-type", "content-disposition", "cache-control"}}
        return Response(status_code=upstream.status_code, content=upstream.content, headers=response_headers)

    return app
