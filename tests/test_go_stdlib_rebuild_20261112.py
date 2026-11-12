"""Counterexamples for the actual Go native/complete-graph migration boundary."""
from copy import deepcopy
import gzip
import json
from pathlib import Path

import pytest

from scripts import go_stdlib_rebuild_20261112 as gate

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'deployment/security/minio-source-20261112-evidence'


def natives():
    return (json.loads((EVIDENCE / 'mcli-new-native.json').read_bytes()),
            json.loads((EVIDENCE / 'mcli-prior-native.json').read_bytes()))


def graphs():
    current = gzip.decompress((EVIDENCE / 'mcli-graph.json.gz').read_bytes())
    previous = gzip.decompress((ROOT / 'deployment/security/minio-html4-evidence/mcli-production-graph-complete.json.gz').read_bytes())
    return current, previous


def test_actual_fixed_elf_preserves_complete_native_module_identity():
    current, previous = natives()
    assert gate.compare_native(current, previous)['nonstdlib_modules_identical']
    assert current['GoVersion'] == 'go1.27.4'


@pytest.mark.parametrize('field,value', [('GoVersion', 'go1.27.3'), ('Path', 'example.invalid/another-target')])
def test_unfixed_or_different_native_target_is_rejected(field, value):
    current, previous = natives()
    current[field] = value
    with pytest.raises(ValueError):
        gate.compare_native(current, previous)


def test_changed_dependency_checksum_cannot_inherit_prior_review():
    current, previous = natives()
    current['Deps'][0]['Sum'] = 'h1:changed-checksum'
    with pytest.raises(ValueError, match='module versions, sums or replacements'):
        gate.compare_native(current, previous)


def test_different_source_commit_cannot_inherit_prior_review():
    current, previous = natives()
    current['Settings'].append({'Key': 'vcs.revision', 'Value': '0' * 40})
    with pytest.raises(ValueError, match='vcs.revision'):
        gate.compare_native(current, previous)


def test_real_windows_linux_graphs_preserve_all_nonstdlib_identities_and_imports():
    current, previous = graphs()
    facts = gate.compare_graphs(current, previous)
    assert facts['complete_import_closure']
    assert facts['nonstdlib_graph_identical']


@pytest.mark.parametrize('change', ['version', 'import', 'missing-package'])
def test_changed_production_graph_is_rejected(change):
    current, previous = graphs()
    packages = gate.decode_graph(current)
    name = next(name for name, row in packages.items() if not row.get('Standard') and row.get('Module'))
    if change == 'version':
        packages[name]['Module']['Version'] = 'v0.0.0-unreviewed'
    elif change == 'import':
        packages[name].setdefault('Imports', []).append('example.invalid/unreviewed-dependency')
    else:
        packages.pop(name)
    changed = '\n'.join(json.dumps(row) for row in packages.values()).encode()
    with pytest.raises(ValueError):
        gate.compare_graphs(changed, previous)


def test_duplicate_graph_fields_cannot_hide_one_import_set():
    with pytest.raises(ValueError, match='Duplicate production graph field'):
        gate.decode_graph(b'{"ImportPath":"one","Imports":[],"Imports":["hidden"]}')


def test_advisory_fixed_version_must_remain_exact():
    advisory = json.loads((ROOT / 'deployment/security/go-stdlib-20261112-evidence/GO-2026-6859.json').read_bytes())
    assert gate.check_advisory(advisory)['fixed_version'] == 'go1.27.4'
    changed = deepcopy(advisory)
    changed['affected'][0]['ranges'][0]['events'][-1]['fixed'] = '1.27.3'
    with pytest.raises(ValueError, match='fixed Go version range'):
        gate.check_advisory(changed)


def scan_identity():
    report = json.loads(gzip.decompress((EVIDENCE / 'raw-trivy.json.gz').read_bytes()))
    config = report['Metadata']['ImageConfig']
    inspection = {'Id': 'sha256:' + 'a' * 64, 'Config': deepcopy(config['config']),
                  'Os': config['os'], 'Architecture': config['architecture']}
    receipt = {'config_digest': report['Metadata']['ImageID'], 'diff_ids': deepcopy(report['Metadata']['DiffIDs']),
               'runtime_config': deepcopy(gate.shared.runtime_config(config['config']))}
    return report, inspection, receipt


def test_saved_archive_config_and_engine_id_are_distinct_verified_identity_nodes():
    report, inspection, receipt = scan_identity()
    assert gate.verify_trivy_source(report, inspection, receipt) == 'portable_config_digest'
    report['Metadata']['ImageID'] = inspection['Id']
    assert gate.verify_trivy_source(report, inspection, receipt) == 'engine_image_id'


@pytest.mark.parametrize('mutation', ['digest', 'layers', 'runtime', 'provenance', 'architecture'])
def test_an_unrelated_scan_cannot_pass_via_a_tag_or_identity_alias(mutation):
    report, inspection, receipt = scan_identity()
    metadata = report['Metadata']
    if mutation == 'digest':
        metadata['ImageID'] = 'sha256:' + 'b' * 64
    elif mutation == 'layers':
        metadata['DiffIDs'][0] = 'sha256:' + 'b' * 64
    elif mutation == 'runtime':
        metadata['ImageConfig']['config']['Env'].append('SYNTHETIC_UNREVIEWED_SETTING=1')
    elif mutation == 'provenance':
        metadata['ImageConfig']['config']['Labels']['org.deepresearch.project.inputs_sha256'] = 'b' * 64
    else:
        metadata['ImageConfig']['architecture'] = 'arm64'
    with pytest.raises(ValueError):
        gate.verify_trivy_source(report, inspection, receipt)


@pytest.mark.parametrize('mutation', [None, 'additional_low', 'missing_low', 'duplicate_high', 'changed_severity'])
def test_complete_raw_migration_only_removes_the_two_fixed_stdlib_rows(tmp_path, mutation):
    prior = (EVIDENCE / 'prior-raw-trivy.json.gz').read_bytes()
    report = json.loads(gzip.decompress((EVIDENCE / 'raw-trivy.json.gz').read_bytes()))
    if mutation == 'additional_low':
        report['Results'][0]['Vulnerabilities'].append({'VulnerabilityID': 'CVE-SYNTHETIC-UNREVIEWED',
            'PkgName': 'synthetic-package', 'InstalledVersion': '1', 'Severity': 'LOW'})
    elif mutation == 'missing_low':
        rows = report['Results'][0]['Vulnerabilities']
        rows.remove(next(row for row in rows if row['Severity'] == 'LOW'))
    elif mutation == 'duplicate_high':
        result = next(row for row in report['Results'] if any(v['Severity'] == 'HIGH' for v in row.get('Vulnerabilities', [])))
        result['Vulnerabilities'].append(deepcopy(next(v for v in result['Vulnerabilities'] if v['Severity'] == 'HIGH')))
    elif mutation == 'changed_severity':
        report['Results'][0]['Vulnerabilities'][0]['Severity'] = 'LOW'
    (tmp_path / 'prior.gz').write_bytes(prior)
    (tmp_path / 'current.gz').write_bytes(gzip.compress(json.dumps(report).encode(), mtime=0))
    profile = {'scan_migration': {'prior': 'prior.gz', 'current': 'current.gz'},
               'evidence_sha256': {'prior.gz': 'fixture', 'current.gz': 'fixture'},
               'expected': {'native_sha256': {'usr/bin/mcli': 'fixture', 'usr/bin/silo': 'fixture'}}}
    if mutation is None:
        facts = gate.verify_scan_migration(profile, root=tmp_path)
        assert facts['fixed_stdlib_rows'] == 2 and facts['unrelated_rows_unchanged']
    else:
        with pytest.raises(ValueError, match='differences exceed'):
            gate.verify_scan_migration(profile, root=tmp_path)
