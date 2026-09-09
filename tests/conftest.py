import pytest
import httpx

from backend.api.app import create_app
from backend.core.config import Settings
from backend.core.auth import issue_development_token
from backend.services.runtime import Runtime


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, demo_mode=True, auth_mode="development", queue_backend="local", document_scan_mode="disabled", data_dir=tmp_path, max_run_seconds=30)


@pytest.fixture
async def runtime(settings):
    rt = await Runtime(settings).start()
    try:
        yield rt
    finally:
        await rt.close()


@pytest.fixture
async def api_client(settings):
    app = create_app(settings)
    async def attach_test_identity(request):
        request.headers["Authorization"] = "Bearer " + issue_development_token(settings, request.headers.get("X-User-ID", "alice"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     event_hooks={"request": [attach_test_identity]}) as client:
            yield app, client
