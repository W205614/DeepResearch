"""Recheck real retained source evidence and reject unrelated/overbroad claims."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts import grafana_source_context_20261110 as gate

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def contexts():
    base = ROOT / gate.BASE
    review = json.loads((ROOT / gate.REVIEW).read_bytes())
    continuity = json.loads((ROOT / gate.CONTINUITY).read_bytes())
    names = {p.relative_to(ROOT).as_posix() for p in base.rglob('*') if p.is_file()}
    for tree in continuity['source_trees']:
        names.update(tree['files_sha256'])
    names.update(p.relative_to(ROOT).as_posix() for p in (ROOT / 'tests/fixtures/milvus-telemetry-example-20261110').glob('*')
                 if p.is_file())
    runtime = {}
    for component in set(gate.COMPONENT.values()):
        files = {'Dockerfile.milvus'} if component == 'milvus' else {'deployment/docker/Dockerfile.' + component}
        if component != 'storage-init':
            files.add('deployment/vendors/' + component + '/source.lock.json')
        names.update(files)
        runtime[component] = {name: gate.build.digest(ROOT / name) for name in files}
    profile = {'evidence_sha256': {name: gate.build.digest(ROOT / name) for name in names},
               'expected': {'source_scan': {'external_runtime_inputs': runtime}}}
    return profile, review


def raw_tuple(review, target):
    original, = [r for r in review['alerts'] if r['target'] == target]
    return {k: original[k] for k in ('id', 'target', 'package', 'version')} | {
        'severity': (original['security_severity'] or 'UNKNOWN').upper()}


def external_proof(review, row):
    original, = [r for r in review['alerts'] if r['target'] == row['target']]
    return {'kind': 'external_graph', 'review': gate.REVIEW, 'target': row['target'],
        'source_sha256': original['source_sha256'], 'profiles': original['profiles'],
        'package_prefixes': review['official_openpgp_advisory']['affected_imports']}


@pytest.mark.parametrize('target', [
    'deployment/vendors/alertmanager/go.mod', 'deployment/vendors/etcd/modules/etcdctl/go.mod',
    'deployment/vendors/etcd/modules/etcdutl/go.mod', 'deployment/vendors/etcd/modules/server/go.mod',
    'deployment/vendors/loki/go.mod', 'deployment/vendors/milvus/modules/go.mod',
    'deployment/vendors/milvus/modules/pkg/go.mod', 'deployment/vendors/otel-collector/source/go.mod',
    'deployment/vendors/prometheus/go.mod', 'deployment/vendors/tempo/go.mod', 'scripts/storage-bootstrap/go.mod'])
def test_every_retained_external_context_reparses_its_actual_graph_native_and_source(contexts, target):
    profile, review = contexts
    row = raw_tuple(review, target)
    gate.external_graph(ROOT, profile, row, external_proof(review, row))


@pytest.mark.parametrize('target', ['deployment/vendors/etcd/modules/go.mod',
    'deployment/vendors/etcd/modules/tests/go.mod', 'deployment/vendors/etcd/modules/tools/mod/go.mod',
    'deployment/vendors/milvus/modules/tests/go_client/go.mod'])
def test_actual_standalone_test_closures_keep_their_own_source_context(contexts, target):
    profile, review = contexts
    standalone = json.loads((ROOT / gate.STANDALONE).read_bytes())
    descriptor, = [r for r in standalone['rows'] if r['target'] == target]
    row = raw_tuple(review, target)
    prefixes = (['go.mongodb.org/mongo-driver/x/mongo/driver/auth/internal/gssapi']
                if row['package'] == 'go.mongodb.org/mongo-driver' else review['official_openpgp_advisory']['affected_imports'])
    proof = {'kind': 'standalone_context', 'review': gate.STANDALONE, 'target': target,
             'source_sha256': descriptor['target_sha256'], 'alert': descriptor['alert'], 'package_prefixes': prefixes}
    gate.standalone_context(ROOT, profile, row, proof)


def test_actual_maintenance_patch_verifies_replacement_without_relabeling_original_fixed(contexts):
    profile, review = contexts
    row = raw_tuple(review, 'deployment/vendors/milvus/modules/examples/telemetry_demo/go.mod')
    row['disposition'] = 'MAINTAINED_REPLACEMENT_VERIFIED'
    proof = {'kind': 'maintained_patch', 'review': gate.MAINTAINED, 'target': row['target'],
             'source_sha256': gate.build.digest(ROOT / row['target'])}
    result = gate.maintained_patch(ROOT, profile, row, proof)
    assert result == {'original_snapshot_fixed': False, 'maintained_replacement_verified': True,
                      'new_application_execution_claimed': False}


@pytest.mark.parametrize('change', ['severity', 'target', 'source_hash', 'profile', 'affected_scope', 'recipe_hash', 'unbound_review'])
def test_external_evidence_cannot_cover_unrelated_or_changed_subjects(contexts, change):
    original_profile, review = contexts
    profile = deepcopy(original_profile)
    row = raw_tuple(review, 'deployment/vendors/alertmanager/go.mod')
    proof = external_proof(review, row)
    if change == 'severity':
        row['severity'] = 'LOW'
    elif change == 'target':
        proof['target'] = 'scripts/storage-bootstrap/go.mod'
    elif change == 'source_hash':
        proof['source_sha256'] = 'a' * 64
    elif change == 'profile':
        proof['profiles'] = ['etcd']
    elif change == 'affected_scope':
        proof['package_prefixes'] = ['unrelated/absent']
    elif change == 'recipe_hash':
        inputs = profile['expected']['source_scan']['external_runtime_inputs']['alertmanager']
        inputs['deployment/docker/Dockerfile.alertmanager'] = 'a' * 64
    else:
        profile['evidence_sha256'].pop(gate.REVIEW)
    with pytest.raises(ValueError):
        gate.external_graph(ROOT, profile, row, proof)


def test_mongo_bson_presence_is_not_misrepresented_as_whole_module_absence(contexts):
    profile, review = contexts
    target = 'deployment/vendors/etcd/modules/tools/mod/go.mod'
    row = raw_tuple(review, target)
    proof = {'kind': 'standalone_context', 'review': gate.STANDALONE, 'target': target,
        'source_sha256': gate.build.digest(ROOT / target), 'alert': 23,
        'package_prefixes': ['go.mongodb.org/mongo-driver']}
    with pytest.raises(ValueError, match='affected package'):
        gate.standalone_context(ROOT, profile, row, proof)


def test_preserved_original_example_cannot_be_labeled_fixed(contexts):
    profile, review = contexts
    row = raw_tuple(review, 'deployment/vendors/milvus/modules/examples/telemetry_demo/go.mod')
    row['disposition'] = 'FIXED'
    proof = {'kind': 'maintained_patch', 'review': gate.MAINTAINED, 'target': row['target'],
             'source_sha256': gate.build.digest(ROOT / row['target'])}
    with pytest.raises(ValueError, match='preserved original'):
        gate.maintained_patch(ROOT, profile, row, proof)
