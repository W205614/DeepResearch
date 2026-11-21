import pytest
import httpx

from backend.api.app import create_app
from backend.core.config import Settings
from backend.core.auth import issue_development_token
from backend.services.runtime import Runtime


@pytest.fixture(scope='session')
def released_v015_root(tmp_path_factory):
    """Replay v0.1.5-bound proofs without applying them to a different checkout."""
    from pathlib import Path
    import subprocess
    import tarfile

    repository = Path(__file__).resolve().parents[1]
    root = tmp_path_factory.mktemp('released-v015')
    revision = 'e6799f73b00144b9ef16b71dedac2759a5cf54af'
    with subprocess.Popen(['git', 'archive', revision], cwd=repository, stdout=subprocess.PIPE) as process:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            for member in archive:
                path = (root / member.name).resolve()
                assert path.is_relative_to(root)
                assert member.isdir() or member.isfile()
                if member.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.extractfile(member).read())
        process.stdout.read()
        assert process.wait() == 0
    return root


@pytest.fixture(scope='session')
def released_v015_git(released_v015_root):
    """Give the archived tree its own Git configuration, index, and refs."""
    from pathlib import Path
    import subprocess

    repository = Path(__file__).resolve().parents[1]
    objects = subprocess.check_output(
        ['git', 'rev-parse', '--path-format=absolute', '--git-path', 'objects'], cwd=repository, text=True).strip()
    subprocess.run(['git', 'init', '-q', str(released_v015_root)], check=True)
    alternates = released_v015_root / '.git/objects/info/alternates'
    alternates.write_bytes(objects.encode('utf-8') + b'\n')
    subprocess.run(['git', 'update-ref', 'HEAD', 'e6799f73b00144b9ef16b71dedac2759a5cf54af'],
                   cwd=released_v015_root, check=True)
    return released_v015_root


@pytest.fixture(scope='session')
def retained_agent_record_root(tmp_path_factory):
    """Replay the retired SoupSieve scope against its actual published inputs."""
    import io
    import json
    from pathlib import Path, PurePosixPath
    import subprocess
    import tarfile
    from scripts import check_agent_full_scope as scope

    repository = Path(__file__).resolve().parents[1]
    profile = json.loads(scope.SOUP_PROFILE_PATH.read_bytes())
    evidence = profile['source_review_evidence']['path']
    review = json.loads((repository / evidence).read_bytes())
    names = [evidence, *(row['path'] for row in review['additional_evidence_files'])]
    raw = subprocess.check_output(['git', 'archive', '0b613f1386bcd7067cb54e236db6e29510c44b16',
                                   '--', *names], cwd=repository)
    root = tmp_path_factory.mktemp('retained-agent-record')
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive:
            if member.isdir():
                continue
            assert member.isfile() and member.name in names
            path = PurePosixPath(member.name)
            assert not path.is_absolute() and '..' not in path.parts
            target = root / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    return root


@pytest.fixture(scope='session')
def retained_agent_prompt_parent_root(tmp_path_factory):
    """Read the old exact Agent review inputs from their real pre-prompt Git revision."""
    import io
    import json
    from pathlib import Path, PurePosixPath
    import subprocess
    import tarfile
    from scripts import check_agent_full_scope as scope

    repository = Path(__file__).resolve().parents[1]
    revision = '9ca6b7a57b850d0da06312ba8749297515897833'
    old_names = {
        'agent-full-scope-20261110', 'agent-ordinary-query-20261112',
        'agent-ordinary-query-20261113', 'agent-reference-locator-20261113',
        'agent-structured-locator-20261113', 'agent-local-attribution-20261113',
        'agent-soupsieve-paths-20261113', 'agent-record-metadata-20261113',
        'agent-soupsieve41-20261115',
    }
    names = set()
    for name in old_names:
        profile_path, _ = scope.profile_identity(name)
        relative = profile_path.relative_to(repository).as_posix()
        profile = json.loads(subprocess.check_output(['git', 'show', revision + ':' + relative], cwd=repository))
        evidence = profile['source_review_evidence']['path']
        review = json.loads(subprocess.check_output(['git', 'show', revision + ':' + evidence], cwd=repository))
        names.update({relative, evidence, *(r['path'] for r in review['additional_evidence_files'])})
    raw = subprocess.check_output(['git', 'archive', revision, '--', *sorted(names)], cwd=repository)
    root = tmp_path_factory.mktemp('retained-agent-before-prompt')
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive:
            if member.isdir():
                continue
            assert member.isfile() and member.name in names
            path = PurePosixPath(member.name)
            assert not path.is_absolute() and '..' not in path.parts
            destination = root / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.extractfile(member).read())
    return root


@pytest.fixture(autouse=True)
def retained_agent_prompt_tests(request, monkeypatch):
    """Replay each Agent profile against the release it actually reviewed."""
    if request.module.__name__.rsplit('.', 1)[-1] != 'test_agent_full_scope':
        return
    from scripts import check_agent_full_scope as scope

    old_parameters = {
        'agent-full-scope-20261110', 'agent-ordinary-query-20261112',
        'agent-ordinary-query-20261113', 'agent-reference-locator-20261113',
        'agent-structured-locator-20261113', 'agent-local-attribution-20261113',
        'agent-soupsieve41-20261115',
    }
    standalone = {
        'test_ordinary_profile_source_snapshot_matches_historical_runtime_manifest',
        'test_current_profile_preserves_exact_five_observed_severity_transitions',
        'test_current_source_review_binds_complete_closure_and_two_source_snapshots',
        'test_reference_locator_profile_preserves_all_retained_technical_facts',
        'test_structured_locator_profile_retains_exact_technical_facts_and_bounds',
        'test_local_attribution_profile_keeps_all_prior_technical_facts',
        'test_ordinary_profile_rejects_modified_historical_source_snapshot',
        'test_ordinary_profile_does_not_pin_the_moving_checkout_as_old_runtime_evidence',
        'test_new_openssl_review_and_supporting_evidence_are_pinned',
    }
    parameter = getattr(request.node, 'callspec', None)
    selected = parameter.params.get('reviewed_inputs') if parameter else None
    name = request.node.originalname
    if selected in old_parameters or (selected is None and name in standalone):
        monkeypatch.setattr(scope, 'ROOT', request.getfixturevalue('retained_agent_prompt_parent_root'))
    elif selected == scope.GAP_PROFILE_NAME:
        monkeypatch.setattr(scope, 'ROOT', request.getfixturevalue('released_v015_root'))
        if name != 'test_ordinary_profile_preserves_findings_and_rejects_the_old_python_closure':
            return
        # The comparison also needs the two pre-v0.1.5 profiles on their own source.
        load = scope.load_profile
        parent = request.getfixturevalue('retained_agent_prompt_parent_root')

        def historical_comparison(profile_name):
            if profile_name not in {scope.ORDINARY_PROFILE_NAME, scope.PROFILE_NAME}:
                return load(profile_name)
            with monkeypatch.context() as scoped:
                scoped.setattr(scope, 'ROOT', parent)
                return load(profile_name)

        monkeypatch.setattr(scope, 'load_profile', historical_comparison)


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
    import io
    import json
    import shutil
    import subprocess
    import zipfile
    from scripts import check_grafana_active_source_retirement as gate
    from scripts import grafana_source_candidate4 as addon

    repository = Path(__file__).resolve().parents[1]
    helpers = repository / 'tests/fixtures/grafana-source-candidate4-historical'
    selectors = repository / 'tests/fixtures/grafana-fixed-20261107/selectors-before'
    selector_names = {'compose.yaml', 'scripts/runtime_ci_inventory.py',
                      '.github/workflows/ci.yml', '.github/workflows/security.yml',
                      '.github/workflows/rag-quality.yml', 'deployment/compose/compose.verify.yaml',
                      '.dockerignore'}
    selection = json.loads((repository / gate.MAINTENANCE_REVIEW_PATH).read_bytes())
    root = tmp_path_factory.mktemp('source18-historical-files')
    # This is a historical replay, not the current repository's authorization.
    # Retain the exact reviewed files and helpers without new versioned go.mod
    # fixtures or access to an old Git object in a shallow CI checkout.
    historical_revision = '0b613f1386bcd7067cb54e236db6e29510c44b16'
    names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only',
        historical_revision, '--', 'deployment/security'], cwd=repository).decode().splitlines()
    records = [name for name in names if Path(name).parent.as_posix() == 'deployment/security'
               and name.endswith('.json')]
    archive = subprocess.check_output(['git', 'archive', '--format=zip', historical_revision,
                                      '--', *records], cwd=repository)
    with zipfile.ZipFile(io.BytesIO(archive)) as sources:
        for name in records:
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(sources.read(name))
    # Later workflows and Compose overlays were not part of this historical
    # review. Current-production selector validation remains unchanged.
    for name in selection['selector_files_sha256']:
        if name.startswith(('.github/workflows/', 'deployment/compose/')):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repository / name, path)
    legacy = json.loads((root / gate.source.old.gfx.POLICY).read_bytes())['binding']
    source2 = json.loads((repository / gate.source.REVIEW_PATH).read_bytes())
    maintenance = json.loads((repository / 'deployment/security/grafana-maintenance-request.json').read_bytes())
    required = (legacy['recipe_files'] | legacy['evidence_files'] | maintenance['files_sha256'] | source2['input_files']
                | source2['evidence_files']
                | selection['input_files'] | selection['selector_files_sha256'])
    html = json.loads((repository / gate.HTML_REVIEW_PATH).read_bytes())
    appendix = json.loads((repository / addon.REVIEW_PATH).read_bytes())
    required |= (html['evidence_files'] | appendix['evidence_files']
                 | {spec['source']: spec['source_sha256'] for spec in appendix['builds'].values()})
    for name, expected in required.items():
        if name in selector_names:
            source = selectors / name
        elif name in {'scripts/fetch_grafana_candidate.py', 'scripts/scan_grafana_candidate.py'}:
            source = helpers / (Path(name).name + '.txt')
        else:
            source = repository / name
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            # Later security upgrades change live locks; replay the exact old
            # contract against its immutable source rather than refreshing it.
            raw = subprocess.check_output(['git', 'show',
                '0b613f1386bcd7067cb54e236db6e29510c44b16:' + name], cwd=repository)
        assert hashlib.sha256(raw).hexdigest() == expected, name
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    review = json.loads((repository / addon.REVIEW_PATH).read_bytes())
    manifests = [row[0] for row in review['fresh_scan_targets']]
    for name in manifests:
        path = root / name
        if not path.exists():
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
    assert gate.selector_files(root) == sorted(selection['selector_files_sha256'])
    assert not subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=root)
    return root


@pytest.fixture(scope='session')
def historical_grafana_candidate_source_root(tmp_path_factory):
    from pathlib import Path
    import hashlib
    import json
    import subprocess
    from scripts import check_grafana_candidate_source_exact2 as gate
    from scripts import check_grafana_candidate_exact28 as candidate
    from scripts import security_review_policy as policy

    repository = Path(__file__).resolve().parents[1]
    root = tmp_path_factory.mktemp('historical-grafana-candidate-source')
    review = json.loads((repository / gate.REVIEW_PATH).read_bytes())
    request = json.loads((repository / candidate.REQUEST_PATH).read_bytes())
    expected = review['input_files'] | review['evidence_files'] | request['files_sha256'] | {
        candidate.REQUEST_PATH.as_posix(): candidate.REQUEST_SHA256,
        gate.REVIEW_PATH: gate.REVIEW_SHA256,
        policy.POLICY_PATH: policy.POLICY_SHA256,
    }
    for name, digest in expected.items():
        raw = (repository / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            selectors = repository / 'tests/fixtures/grafana-fixed-20261107/selectors-before' / name
            helper = repository / 'tests/fixtures/grafana-source-candidate4-historical' / (Path(name).name + '.txt')
            candidates = [path.read_bytes() for path in (selectors, helper) if path.is_file()]
            candidates.append(subprocess.check_output(['git', 'show',
                '0b613f1386bcd7067cb54e236db6e29510c44b16:' + name], cwd=repository))
            matches = [value for value in candidates if hashlib.sha256(value).hexdigest() == digest]
            if not matches:
                revisions = subprocess.check_output(['git', 'log', '--all', '--format=%H', '--', name],
                                                   cwd=repository).decode().splitlines()
                for revision in revisions:
                    value = subprocess.check_output(['git', 'show', revision + ':' + name], cwd=repository)
                    if hashlib.sha256(value).hexdigest() == digest:
                        matches.append(value)
                        break
            assert matches, name
            raw = matches[0]
        assert hashlib.sha256(raw).hexdigest() == digest, name
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return root


@pytest.fixture
def historical_grafana_source_pins(monkeypatch):
    import ast
    from pathlib import Path
    import subprocess
    from scripts import grafana_image_disposition as grafana
    from scripts import check_fs_source_applicability as source
    from scripts import security_review_policy as policy

    repository = Path(__file__).resolve().parents[1]
    # Replay the original exact constants too. The production pins remain current,
    # and monkeypatch restores them after this one historical evaluation.
    for module, names in [
        (grafana, {'APPROVED_BINDING_SHA256'}),
        (source, {'EXPECTED_SCOPE_SHA256', 'TECHNICAL_REVIEW_SHA256'}),
        (policy, {'POLICY_SHA256'}),
    ]:
        relative = Path(module.__file__).resolve().relative_to(repository).as_posix()
        raw = subprocess.check_output(['git', 'show',
            '0b613f1386bcd7067cb54e236db6e29510c44b16:' + relative], cwd=repository)
        for node in ast.parse(raw).body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in names:
                        monkeypatch.setattr(module, target.id, ast.literal_eval(node.value))


@pytest.fixture(scope='session')
def retained_grafana_context_root(tmp_path_factory):
    """Original external graphs and CSS proof retain their actual source tree."""
    import hashlib
    import io
    import json
    from pathlib import Path, PurePosixPath
    import subprocess
    import tarfile
    from scripts import grafana_source_context_20261110 as context
    from scripts import check_grafana_source_scan_20261112 as python_scope

    repository = Path(__file__).resolve().parents[1]
    revision = '0b613f1386bcd7067cb54e236db6e29510c44b16'
    continuity = json.loads((repository / context.CONTINUITY).read_bytes())
    proof = json.loads((repository / python_scope.PYTHON_REVIEW).read_bytes())
    names = set(subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', revision, '--',
        context.BASE, 'tests/fixtures/milvus-telemetry-example-20261110'], cwd=repository).decode().splitlines())
    for tree in continuity['source_trees']:
        names.update(tree['files_sha256'])
    for component in set(context.COMPONENT.values()):
        names.add('Dockerfile.milvus' if component == 'milvus' else 'deployment/docker/Dockerfile.' + component)
        if component != 'storage-init':
            names.add('deployment/vendors/' + component + '/source.lock.json')
    names.update({python_scope.PYTHON_REVIEW, 'uv.lock'})
    names.update(proof['support_sha256'])
    names.update(proof['current_application_files_sha256'])
    raw = subprocess.check_output(['git', 'archive', revision, '--', *sorted(names)], cwd=repository)
    root = tmp_path_factory.mktemp('retained-grafana-external-contexts')
    executables = []
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive:
            if member.isdir():
                continue
            assert member.isfile() and member.name in names
            path = PurePosixPath(member.name)
            assert not path.is_absolute() and '..' not in path.parts
            destination = root / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.extractfile(member).read())
            if member.mode & 0o111:
                executables.append(member.name)
    expected = proof['support_sha256'] | proof['current_application_files_sha256'] | {'uv.lock': proof['uv_lock_sha256']}
    for tree in continuity['source_trees']:
        expected.update(tree['files_sha256'])
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest for name, digest in expected.items())
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True)
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'add', '--', *sorted(names)], cwd=root,
                   check=True, capture_output=True)
    if executables:
        subprocess.run(['git', 'update-index', '--chmod=+x', '--', *executables], cwd=root,
                       check=True, capture_output=True)
    subprocess.run(['git', '-c', 'user.name=Historical test fixture', '-c', 'user.email=fixture@example.invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '--no-gpg-sign', '-m', 'Exact retained source contexts'],
                   cwd=root, check=True, capture_output=True)
    return root


@pytest.fixture(autouse=True)
def retained_bound_grafana_tests(request, monkeypatch):
    """Keep hash-bound original tests byte-identical while replaying their inputs."""
    module = request.module
    name = module.__name__.rsplit('.', 1)[-1]
    if name not in {'test_milvus_telemetry_maintenance', 'test_grafana_python_source_scope_20261113'}:
        return
    root = request.getfixturevalue('retained_grafana_context_root')
    if name == 'test_milvus_telemetry_maintenance':
        monkeypatch.setattr(module, 'ROOT', root)
        monkeypatch.setattr(module, 'EVIDENCE', root / 'deployment/security/code-scanning-review-20261110-evidence/telemetry-example')
    else:
        monkeypatch.setattr(module.gate.python_source_scope, '__defaults__', (root,))
