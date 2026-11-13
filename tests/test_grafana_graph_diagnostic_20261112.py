from copy import deepcopy
from pathlib import Path
import gzip
import hashlib
import io
import json
import tarfile

import pytest
import yaml

from scripts import capture_go_graph_diagnostic_20261112 as diagnostic

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'usr/share/deepresearch/grafana-candidate/'
GRAPH = PREFIX + 'graphs/main/main.json.gz'
PROVENANCE = PREFIX + 'provenance/plugins/mysql.json'
SCRIPT = 'scripts/capture_go_graph_diagnostic_20261112.py'


def row(path, raw):
    return {'path': path, 'type': 'file', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value).encode())


def grafana_export(root, mutation=None):
    build = root / '.cache/grafana-build'
    payload = build / 'actual-payload'
    payload.mkdir(parents=True)
    old = {path: gzip.compress(b'{"before":1}\n', mtime=0) if path.endswith('.gz') else b'{"before":1}\n'
           for path in diagnostic.GRAFANA_TARGETS}
    actual = dict(old)
    actual[GRAPH] = gzip.compress(b'{"actual":"raw graph"}\n', mtime=0)
    actual[PROVENANCE] = b'{"actual":"raw provenance"}\n'
    expected_rows = [row(path, raw) for path, raw in old.items()]
    canonical = json.dumps(expected_rows, sort_keys=True, separators=(',', ':')).encode()
    baseline = root / 'deployment/security/grafana-source-20261112-evidence/runtime-manifest.json.gz'
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(gzip.compress(canonical, mtime=0))
    profile = {'profile': 'grafana-source-20261112',
               'expected': {'runtime_manifest_sha256': hashlib.sha256(canonical).hexdigest()}}
    if mutation == 'baseline':
        profile['expected']['runtime_manifest_sha256'] = '0' * 64
    write_json(root / 'deployment/security/grafana-source-20261112-profile.json', profile)
    actual['etc/unrelated-private-file'] = b'unrelated data must not be copied'
    actual_rows = [row(path, raw) for path, raw in actual.items()]
    canonical = json.dumps(actual_rows, sort_keys=True, separators=(',', ':')).encode()
    (payload / 'runtime-manifest.json.gz').write_bytes(gzip.compress(canonical, mtime=0))
    write_json(build / 'build-receipt.json', {'profile': 'grafana-source-20261112',
               'runtime_manifest_sha256': hashlib.sha256(canonical).hexdigest(), 'image_id': 'sha256:' + 'a' * 64})
    with tarfile.open(payload / 'rootfs.tar', 'w') as archive:
        for path, raw in actual.items():
            if mutation == 'missing' and path == GRAPH:
                continue
            content = raw + (b'changed' if mutation == 'hash' and path == GRAPH else b'')
            member = tarfile.TarInfo(path)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return build, actual


def test_grafana_collects_only_actual_differing_static_documents_and_retains_original_bytes(tmp_path):
    assert len(diagnostic.GRAFANA_TARGETS) == 39
    assert all(path.startswith(PREFIX + 'graphs/') or path.startswith(PREFIX + 'provenance/plugins/')
               for path in diagnostic.GRAFANA_TARGETS)
    build, raw = grafana_export(tmp_path)
    result = diagnostic.collect(build, root=tmp_path, artifact='grafana')
    expected_names = {diagnostic.GRAFANA_TARGETS[path] for path in (GRAPH, PROVENANCE)}
    assert {p.name for p in (build / 'diagnostic-graphs').iterdir()} == expected_names | {'receipt.json'}
    assert result['selected_runtime_paths'] == sorted([GRAPH, PROVENANCE])
    assert result['uncollected_changed_runtime_paths'] == ['etc/unrelated-private-file']
    for path in (GRAPH, PROVENANCE):
        assert (build / 'diagnostic-graphs' / diagnostic.GRAFANA_TARGETS[path]).read_bytes() == raw[path]
    assert result['build_acceptance_claim'] is False and result['security_gate_claim'] is False
    assert result['raw_graphs_modified'] is False


@pytest.mark.parametrize('mutation', ['baseline', 'hash', 'missing'])
def test_grafana_diagnostic_rejects_unbound_baseline_or_invalid_actual_payload(tmp_path, mutation):
    build, _ = grafana_export(tmp_path, mutation)
    with pytest.raises(ValueError):
        diagnostic.collect(build, root=tmp_path, artifact='grafana')
    assert not (build / 'diagnostic-graphs').exists()


def test_artifact_selector_cannot_name_arbitrary_tar_members(tmp_path):
    with pytest.raises(ValueError, match='fixed source-build artifacts'):
        diagnostic.collect(tmp_path, root=tmp_path, artifact='../../etc/passwd')


def steps_for(workflow, job):
    return yaml.safe_load((ROOT / '.github/workflows' / workflow).read_text(encoding='utf-8'))['jobs'][job]['steps']


def assert_workflow_contract(steps, *, security):
    selected = [(i, step) for i, step in enumerate(steps)
                if SCRIPT in step.get('run', '') and '--artifact grafana' in step['run']]
    assert len(selected) == 1
    index, collector = selected[0]
    assert 'always()' in collector['if'] and 'failure()' in collector['if']
    for name in ('build-receipt.json', 'actual-payload/runtime-manifest.json.gz', 'actual-payload/rootfs.tar'):
        assert f"hashFiles('.cache/eval/grafana-source-build/{name}') != ''" in collector['if']
    if security:
        assert "matrix.artifact == 'grafana'" in collector['if']
    assert not collector.get('continue-on-error')
    builders = [s for s in steps[:index] if s.get('uses') == './.github/actions/reviewed-source-image'
                and s.get('with', {}).get('artifact') == 'grafana']
    assert builders and all(not s.get('continue-on-error') for s in builders)
    uploads = [s for i, s in enumerate(steps) if i > index
               and '.cache/eval/grafana-source-build/diagnostic-graphs/*.json.gz'
               in s.get('with', {}).get('path', '').splitlines()]
    assert len(uploads) == 1 and uploads[0]['if'] == 'always()'
    assert '.cache/eval/grafana-source-build/diagnostic-graphs/*.json' in uploads[0]['with']['path'].splitlines()
    assert not any('rootfs.tar' in s.get('with', {}).get('path', '') for s in steps)


@pytest.mark.parametrize(('workflow', 'job', 'security'), [('ci.yml', 'enterprise-compose-smoke', False),
                                                        ('security.yml', 'runtime-image-scan', True)])
def test_grafana_workflow_preserves_failures_and_only_uploads_fixed_diagnostic_documents(workflow, job, security):
    assert_workflow_contract(steps_for(workflow, job), security=security)


@pytest.mark.parametrize('mutation', ['missing-input', 'wrong-image', 'swallowed-build', 'discarded-failure-raw'])
def test_grafana_workflow_rejects_bypasses_or_unbounded_collection(mutation):
    steps = deepcopy(steps_for('security.yml', 'runtime-image-scan'))
    collector = next(s for s in steps if SCRIPT in s.get('run', '') and '--artifact grafana' in s['run'])
    if mutation == 'missing-input':
        collector['if'] = collector['if'].replace(
            "hashFiles('.cache/eval/grafana-source-build/actual-payload/rootfs.tar') != ''", 'true')
    elif mutation == 'wrong-image':
        collector['if'] = collector['if'].replace("matrix.artifact == 'grafana'", 'true')
    elif mutation == 'swallowed-build':
        next(s for s in steps if s.get('uses') == './.github/actions/reviewed-source-image'
             and s['with']['artifact'] == 'grafana')['continue-on-error'] = True
    else:
        next(s for s in steps if 'grafana-source-build/diagnostic-graphs/*.json.gz'
             in s.get('with', {}).get('path', ''))['if'] = 'success()'
    with pytest.raises(AssertionError):
        assert_workflow_contract(steps, security=True)
