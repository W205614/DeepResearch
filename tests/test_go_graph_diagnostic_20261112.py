from pathlib import Path
from copy import deepcopy
import gzip
import hashlib
import io
import json
import tarfile

import pytest
import yaml

from scripts import capture_go_graph_diagnostic_20261112 as diagnostic

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = 'scripts/capture_go_graph_diagnostic_20261112.py'


def actual_export(root, mutation=None):
    build = root / '.cache/build'
    payload = build / 'actual-payload'
    payload.mkdir(parents=True)
    raw = {'mcli-graph.json': b'{"ImportPath":"example.invalid/mcli"}\n',
           'silo-graph.json': b'{"ImportPath":"example.invalid/silo"}\n'}
    rows = [{'path': name, 'type': 'file', 'bytes': len(raw[filename]),
             'sha256': hashlib.sha256(raw[filename]).hexdigest()}
            for name, filename in diagnostic.TARGETS.items()]
    canonical = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    (payload / 'runtime-manifest.json.gz').write_bytes(gzip.compress(canonical, mtime=0))
    receipt = {'profile': 'minio-source-20261112', 'image_id': 'sha256:' + 'a' * 64,
               'runtime_manifest_sha256': hashlib.sha256(canonical).hexdigest()}
    if mutation == 'receipt':
        receipt['runtime_manifest_sha256'] = '0' * 64
    (build / 'build-receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    with tarfile.open(payload / 'rootfs.tar', 'w') as archive:
        for index, (name, filename) in enumerate(diagnostic.TARGETS.items()):
            if index == 0 and mutation == 'missing':
                continue
            content = raw[filename] + (b'changed' if index == 0 and mutation == 'hash' else b'')
            member = tarfile.TarInfo(name)
            member.size = len(content)
            if index == 0 and mutation == 'symlink':
                member.type, member.linkname, member.size = tarfile.SYMTYPE, '/etc/passwd', 0
            if index == 0 and mutation == 'oversize':
                member.size = diagnostic.MAX_GRAPH_BYTES + 1
                archive.addfile(member, io.BytesIO(b'x' * member.size))
            else:
                archive.addfile(member, io.BytesIO(content))
            if index == 0 and mutation == 'duplicate':
                archive.addfile(member, io.BytesIO(content))
        unrelated = tarfile.TarInfo('unrelated-file.txt')
        unrelated.size = 6
        archive.addfile(unrelated, io.BytesIO(b'ignore'))
    return build, raw


def test_diagnostic_copies_only_two_original_graphs_and_denies_acceptance_claims(tmp_path):
    build, raw = actual_export(tmp_path)
    result = diagnostic.collect(build, root=tmp_path)
    output = build / 'diagnostic-graphs'
    assert {path.name for path in output.iterdir()} == {*raw, 'receipt.json'}
    for name, expected in raw.items():
        assert (output / name).read_bytes() == expected
    assert result['operational_collection'] is True and result['diagnostic_only'] is True
    assert result['build_acceptance_claim'] is False and result['security_gate_claim'] is False
    assert result['raw_graphs_modified'] is False


@pytest.mark.parametrize('mutation', ['receipt', 'missing', 'hash', 'symlink', 'duplicate', 'oversize'])
def test_mismatched_missing_repeated_or_nonregular_raw_evidence_is_rejected(tmp_path, mutation):
    build, _ = actual_export(tmp_path, mutation)
    with pytest.raises(ValueError):
        diagnostic.collect(build, root=tmp_path)
    assert not (build / 'diagnostic-graphs').exists()


def test_existing_output_is_not_overwritten(tmp_path):
    build, _ = actual_export(tmp_path)
    diagnostic.collect(build, root=tmp_path)
    with pytest.raises(ValueError, match='new directory'):
        diagnostic.collect(build, root=tmp_path)


def test_evidence_outside_checkout_cache_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='checkout cache'):
        diagnostic.collect(tmp_path, root=tmp_path)


def steps_for(workflow, job):
    return yaml.safe_load((ROOT / '.github/workflows' / workflow).read_text(encoding='utf-8'))['jobs'][job]['steps']


def assert_workflow_contract(steps, *, security):
    selected = [(i, step) for i, step in enumerate(steps) if SCRIPT in step.get('run', '') and '--artifact grafana' not in step['run']]
    assert len(selected) == 1
    index, step = selected[0]
    builder = next(s for s in steps[:index] if 'scripts/build_go_stdlib_security_20261112.py' in s.get('run', ''))
    assert not builder.get('continue-on-error') and not step.get('continue-on-error')
    assert 'always()' in step['if'] and 'failure()' in step['if']
    for name in ('build-receipt.json', 'actual-payload/runtime-manifest.json.gz', 'actual-payload/rootfs.tar'):
        assert f"hashFiles('.cache/eval/minio-source-build/{name}') != ''" in step['if']
    if security:
        assert "matrix.artifact == 'minio'" in step['if']
    assert step['run'] == ('uv run --frozen --no-dev python ' + SCRIPT
                           + ' --build-dir .cache/eval/minio-source-build')
    uploads = [s for i, s in enumerate(steps) if i > index
               and '.cache/eval/minio-source-build/diagnostic-graphs/*.json'
               in s.get('with', {}).get('path', '').splitlines()]
    assert len(uploads) == 1 and uploads[0]['if'] == 'always()'
    assert not any('rootfs.tar' in s.get('with', {}).get('path', '') for s in steps)


@pytest.mark.parametrize(('workflow', 'job', 'security'), [
    ('ci.yml', 'real-components', False), ('ci.yml', 'enterprise-compose-smoke', False),
    ('security.yml', 'runtime-image-scan', True)])
def test_only_failed_minio_builds_with_all_actual_inputs_collect_bounded_evidence(workflow, job, security):
    assert_workflow_contract(steps_for(workflow, job), security=security)


@pytest.mark.parametrize('mutation', ['missing-input-guard', 'other-security-image', 'swallowed-build', 'lost-failure-artifact'])
def test_diagnostic_does_not_hide_failures_or_capture_unrelated_images(mutation):
    steps = deepcopy(steps_for('security.yml', 'runtime-image-scan'))
    collector = next(s for s in steps if SCRIPT in s.get('run', ''))
    if mutation == 'missing-input-guard':
        collector['if'] = collector['if'].replace(
            "hashFiles('.cache/eval/minio-source-build/actual-payload/rootfs.tar') != ''", 'true')
    elif mutation == 'other-security-image':
        collector['if'] = collector['if'].replace("matrix.artifact == 'minio'", 'true')
    elif mutation == 'swallowed-build':
        next(s for s in steps if 'scripts/build_go_stdlib_security_20261112.py' in s.get('run', ''))['continue-on-error'] = True
    else:
        next(s for s in steps if 'diagnostic-graphs/*.json' in s.get('with', {}).get('path', ''))['if'] = 'success()'
    with pytest.raises(AssertionError):
        assert_workflow_contract(steps, security=True)
