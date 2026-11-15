"""The new runtime must retain a complete graph bound to the actual ELF closure."""
import copy
import gzip

import pytest

from scripts import check_minio_otel_20261115 as gate


@pytest.fixture(scope='module')
def actual_prior_graph():
    profile = gate.prior.load_profile()
    paths = profile['go_source_bridge']['silo']
    native = gate.prior.read(gate.ROOT / paths['native'])
    graph = gate.prior.common.decode_graph(gzip.decompress((gate.ROOT / paths['graph']).read_bytes()))
    return graph, native


def test_complete_real_graph_matches_the_actual_native_identity(actual_prior_graph):
    graph, native = actual_prior_graph
    gate.verify_graph_native(graph, native)


@pytest.mark.parametrize('change', ['entry', 'checksum', 'replacement', 'missing-module',
                                   'missing-main', 'disconnected-package'])
def test_wrong_entry_module_or_incomplete_closure_is_rejected(actual_prior_graph, change):
    graph, native = copy.deepcopy(actual_prior_graph)
    dependency = next(row for row in native['Deps'] if 'Sum' in row and 'Replace' not in row)
    if change == 'entry':
        native['Path'] = 'not/the/minio/entry'
    elif change == 'checksum':
        dependency['Sum'] = 'h1:unreviewed'
    elif change == 'replacement':
        dependency['Replace'] = {'Path': 'example.invalid/replacement', 'Version': 'v1.0.0', 'Sum': 'h1:x'}
    elif change == 'missing-module':
        native['Deps'].remove(dependency)
    elif change == 'missing-main':
        graph[native['Path']]['Module']['Main'] = False
    else:
        graph['not/in/entry/closure'] = {'ImportPath': 'not/in/entry/closure', 'Standard': True}
    with pytest.raises(ValueError):
        gate.verify_graph_native(graph, native)


@pytest.fixture
def actual_preparation():
    evidence = gate.ROOT / 'deployment/security/minio-otel-source-20261115-evidence'
    receipt = gate.prior.read(evidence / 'build-receipt.json')
    inspection = gate.prior.read(evidence / 'image-inspect.json')
    rows = gate.prior.read_gzip(evidence / 'runtime-manifest.json.gz')
    profile = {'source_inputs': receipt['source_inputs'],
               'runtime_evidence': evidence.relative_to(gate.ROOT).as_posix() + '/runtime-manifest.json.gz',
               'expected': {name: receipt[name] for name in ('runtime_config', 'native_sha256',
                                                            'runtime_manifest_sha256')}}
    facts = {name: receipt[name] for name in ('image_id', 'config_digest', 'diff_ids',
                                              'runtime_manifest_sha256')}
    facts.update(inspection=inspection, runtime_manifest=rows)
    return facts, receipt, profile


def test_actual_preparation_is_not_normal_build_approval(actual_preparation):
    gate.verify_build(*actual_preparation, allow_preparation=True)
    with pytest.raises(ValueError):
        gate.verify_build(*actual_preparation)


@pytest.mark.parametrize('change', ['runtime-config', 'input-label', 'source-label', 'preparation',
                                   'receipt-raw-manifest', 'unrelated-runtime-file', 'missing-runtime-file'])
def test_build_labels_preparation_config_and_complete_payload_are_bound(actual_preparation, change):
    facts, receipt, _profile = actual_preparation
    if change == 'runtime-config':
        facts['inspection']['Config']['Env'].append('UNREVIEWED=1')
    elif change == 'input-label':
        facts['inspection']['Config']['Labels']['org.deepresearch.project.inputs_sha256'] = '0' * 64
    elif change == 'source-label':
        facts['inspection']['Config']['Labels']['org.opencontainers.image.revision'] = '0' * 40
    elif change == 'preparation':
        receipt['preparation_only'] = False
    elif change == 'receipt-raw-manifest':
        receipt['runtime_manifest_sha256'] = '0' * 64
    elif change == 'missing-runtime-file':
        facts['runtime_manifest'].pop()
    else:
        row = next(row for row in facts['runtime_manifest']
                   if row['type'] == 'file' and row['path'] not in gate.CHANGED_RUNTIME_FILES
                   and row['path'] != gate.prior.DATABASE_PATH)
        row['sha256'] = '0' * 64
    with pytest.raises(ValueError):
        gate.verify_build(*actual_preparation, allow_preparation=True)
