"""Current source coverage remains exact across the historical evidence migration."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts import source_dependency_migration_20261115 as migration
from scripts.go_lock_upgrade_20261122 import previous_source

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def review():
    return json.loads((ROOT / migration.REVIEW).read_bytes())


def test_current_migration_retains_all_original_and_new_findings(review):
    migration.verify_observed(review, review)
    assert len(review['retained_findings']) == 71
    assert len(review['additional_historical_otel_findings']) == 29
    assert review['risk_acceptance'] is review['raw_report_modified'] is False


@pytest.mark.parametrize('change', ['target', 'inventory', 'extra', 'missing', 'duplicate', 'severity'])
def test_unknown_source_changes_fail_closed(review, change):
    observed = deepcopy(review)
    if change == 'target':
        observed['scan_targets'].pop()
    elif change == 'inventory':
        name = next(iter(observed['target_inventory_sha256']))
        observed['target_inventory_sha256'][name] = '0' * 64
    elif change == 'extra':
        row = deepcopy(observed['findings'][0])
        row['id'] = 'UNREVIEWED'
        observed['findings'].append(row)
    elif change == 'missing':
        observed['findings'].pop()
    elif change == 'duplicate':
        observed['findings'][-1] = deepcopy(observed['findings'][0])
    else:
        observed['findings'][0]['severity'] = 'CRITICAL'
    with pytest.raises(ValueError):
        migration.verify_observed(observed, review)


@pytest.fixture
def current_graph(tmp_path, monkeypatch):
    import gzip
    import hashlib

    def digest(raw):
        return hashlib.sha256(raw).hexdigest()

    inputs = tmp_path / 'source/go.mod'
    inputs.parent.mkdir()
    inputs.write_bytes(b'module fixture\n')
    graph = {'schema': 'current-source-context-20261115-v1', 'package_count': 2,
             'packages': [{'ImportPath': 'fixture/main', 'Imports': ['fixture/dependency']},
                          {'ImportPath': 'fixture/dependency', 'Imports': []}]}
    receipt = {'profile': 'alertmanager', 'exit_code': 0, 'raw_bytes': 1,
               'command': ['go', 'list', '-deps', '-json', '-mod=readonly', './cmd/alertmanager'],
               'raw_sha256': digest(b'raw parser fixture'), 'raw_graph_modified': False,
               'projection_preserves_all_packages_imports_and_module_identities': True,
               'source_inputs': {'source/go.mod': digest(inputs.read_bytes())},
               'environment': {'GOOS': 'linux', 'GOARCH': 'amd64', 'GOAMD64': 'v1',
                               'CGO_ENABLED': '0', 'GOWORK': 'off'},
               'compiler': 'go version go1.27.4 linux/amd64', 'package_count': 2}

    def seal():
        graph_raw = json.dumps(graph).encode()
        graph_path = tmp_path / 'graph.json.gz'
        graph_path.write_bytes(gzip.compress(graph_raw, mtime=0))
        receipt['projection_raw_sha256'] = digest(graph_raw)
        receipt['projection_sha256'] = digest(graph_path.read_bytes())
        receipt_path = tmp_path / 'receipt.json'
        receipt_path.write_bytes(json.dumps(receipt).encode())
        index = {'schema': 'current-source-context-index-20261115-v1', 'contexts': {
            'alertmanager': {'receipt': 'receipt.json', 'receipt_sha256': digest(receipt_path.read_bytes()),
                             'graph': 'graph.json.gz', 'graph_sha256': digest(graph_path.read_bytes())}}}
        index_path = tmp_path / migration.CONTEXTS
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_bytes(json.dumps(index).encode())
        monkeypatch.setattr(migration, 'CONTEXTS_SHA256', digest(index_path.read_bytes()))
        review_path = tmp_path / migration.REVIEW
        review_path.write_bytes(json.dumps({'schema': 'source-dependency-migration-20261115-v1',
            'source_inputs': {}, 'contexts_sha256': digest(index_path.read_bytes())}).encode())
        monkeypatch.setattr(migration, 'REVIEW_SHA256', digest(review_path.read_bytes()))

    seal()
    return tmp_path, graph, receipt, seal


def test_current_context_loader_rechecks_real_parser_and_input_boundary(current_graph):
    root, _, _, _ = current_graph
    receipt, packages = migration.load_current_context('alertmanager', root)
    assert receipt['exit_code'] == 0
    assert set(packages) == {'fixture/main', 'fixture/dependency'}
    (root / 'source/go.mod').write_bytes(b'module changed\n')
    with pytest.raises(ValueError, match='graph input changed'):
        migration.load_current_context('alertmanager', root)


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'error', 'deps', 'mapping', 'cgo', 'compiler', 'failed'])
def test_current_context_rejects_incomplete_or_wrong_subject_even_with_bound_bytes(current_graph, mutation):
    root, graph, receipt, seal = current_graph
    if mutation == 'missing':
        graph['packages'][0]['Imports'].append('fixture/missing')
    elif mutation == 'duplicate':
        graph['packages'][1] = dict(graph['packages'][0])
    elif mutation == 'error':
        graph['packages'][0]['Error'] = {'Err': 'incomplete import'}
    elif mutation == 'deps':
        graph['packages'][0]['Deps'] = ['fixture/missing']
    elif mutation == 'mapping':
        graph['packages'][0]['ImportMap'] = {'alias': 'fixture/missing'}
    elif mutation == 'cgo':
        graph['packages'][0]['Imports'].append('C')
    elif mutation == 'compiler':
        receipt['compiler'] = 'go version go1.27.3 linux/amd64'
    else:
        receipt['exit_code'] = 1
    seal()
    with pytest.raises(ValueError):
        migration.load_current_context('alertmanager', root)


def test_duplicate_frozen_finding_cannot_become_a_new_baseline(review):
    review['findings'].append(deepcopy(review['findings'][0]))
    with pytest.raises(ValueError):
        migration.verify_observed(review, review)


def test_actual_minio_source_rows_bind_retained_complete_graphs_and_official_packages(review):
    result = migration.verify_minio_findings(previous_source(ROOT), review['additional_minio_findings'], review)
    assert {row['context'] for row in result} == {'minio_silo', 'minio_mcli'}
    assert all(row['current_affected_html_or_openpgp_packages'] == []
               and row['libraries_upgraded_claimed'] is False for row in result)


@pytest.mark.parametrize('change', ['extra', 'missing', 'duplicate', 'target', 'severity', 'version'])
def test_minio_source_migration_cannot_expand_exact_scope(review, change):
    rows = deepcopy(review['additional_minio_findings'])
    if change == 'extra':
        rows.append({**rows[0], 'id': 'UNREVIEWED'})
    elif change == 'missing':
        rows.pop()
    elif change == 'duplicate':
        rows.append(deepcopy(rows[0]))
    else:
        rows[0][change] = 'LOW' if change == 'severity' else 'DIFFERENT'
    with pytest.raises(ValueError, match='three exact source tuples'):
        migration.verify_minio_findings(ROOT, rows, review)
