import pytest
import httpx

from backend.api.app import create_app
from backend.core.config import Settings
from backend.core.auth import issue_development_token
from backend.services.runtime import Runtime


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, demo_mode=True, auth_mode="development", queue_backend="local",
                    document_scan_mode="disabled", data_dir=tmp_path, max_run_seconds=30,
                    reranker_enabled=False)


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


@pytest.fixture(scope='session')
def historical_grafana_maintenance_root(tmp_path_factory):
    from pathlib import Path
    import hashlib
    import json
    import shutil
    import subprocess
    from scripts import check_grafana_active_source_retirement as gate
    from scripts import grafana_source_candidate4 as addon

    repository = Path(__file__).resolve().parents[1]
    helpers = repository / 'tests/fixtures/grafana-source-candidate4-historical'
    selectors = repository / 'tests/fixtures/grafana-fixed-20261107/selectors-before'
    selector_names = {'compose.yaml', 'scripts/runtime_ci_inventory.py',
                      '.github/workflows/ci.yml', '.github/workflows/security.yml'}
    root = tmp_path_factory.mktemp('source18-historical-files')
    # This is a historical replay, not the current repository's authorization.
    # Retain the exact reviewed files and helpers without new versioned go.mod
    # fixtures or access to an old Git object in a shallow CI checkout.
    for directory in ['deployment/security', 'deployment/vendors', 'deployment/docker',
                      'deployment/compose', '.github/workflows']:
        shutil.copytree(repository / directory, root / directory,
                        ignore=shutil.ignore_patterns('grafana-fixed-20261107*'))
    selection = json.loads((repository / gate.MAINTENANCE_REVIEW_PATH).read_bytes())
    legacy = json.loads((repository / gate.source.old.gfx.POLICY).read_bytes())['binding']
    source2 = json.loads((repository / gate.source.REVIEW_PATH).read_bytes())
    maintenance = json.loads((repository / 'deployment/security/grafana-maintenance-request.json').read_bytes())
    required = (legacy['recipe_files'] | legacy['evidence_files'] | maintenance['files_sha256'] | source2['input_files']
                | source2['evidence_files']
                | selection['input_files'] | selection['selector_files_sha256'])
    for name, expected in required.items():
        if name in selector_names:
            source = selectors / name
        elif name in {'scripts/fetch_grafana_candidate.py', 'scripts/scan_grafana_candidate.py'}:
            source = helpers / (Path(name).name + '.txt')
        else:
            source = repository / name
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
        path = root / name
        if path.is_file() and name not in selector_names:
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(source.read_bytes())
    review = json.loads((repository / addon.REVIEW_PATH).read_bytes())
    manifests = [row[0] for row in review['fresh_scan_targets']]
    for name in manifests:
        path = root / name
        if path.exists():
            assert path.read_bytes() == (repository / name).read_bytes()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((repository / name).read_bytes())
    for name in ['.gitattributes']:
        (root / name).write_bytes((repository / name).read_bytes())
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True)
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'add', '--', *manifests],
                   cwd=root, check=True, capture_output=True)
    subprocess.run(['git', '-c', 'user.name=Historical test fixture',
                    '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                    'commit', '--no-gpg-sign', '-m', 'Retained historical manifest tree'],
                   cwd=root, check=True, capture_output=True)
    assert gate.source.old.committed_manifest_targets(root, 'HEAD') == review['fresh_scan_targets']
    assert not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=root)
    return root
