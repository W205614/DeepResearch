import copy
import gzip
import json
from pathlib import Path

import pytest

from scripts import check_minio_os_20261113 as gate


def artifact():
    return {'id': 'actual-id', 'name': 'library', 'version': '1', 'type': 'rpm',
            'metadata': {'architecture': 'x86_64'},
            'locations': [{'path': '/var/lib/rpm/rpmdb.sqlite', 'layerID': 'sha256:actual',
                           'accessPath': '/var/lib/rpm/rpmdb.sqlite'}]}


def test_full_sbom_binds_all_catalogued_fields_except_transaction_layer():
    original = {'artifacts': [artifact()]}
    changed = copy.deepcopy(original)
    changed['artifacts'][0]['locations'][0]['layerID'] = 'sha256:other-actual-transaction'
    assert gate.sbom_inventory(original) == gate.sbom_inventory(changed)
    changed['artifacts'][0]['metadata']['architecture'] = 'ppc64'
    assert gate.sbom_inventory(original) != gate.sbom_inventory(changed)


@pytest.mark.parametrize('field,value', [('name', 'other'), ('version', '2'), ('type', 'go-module'),
                                        ('id', 'missing'), ('locations', [])])
def test_match_must_belong_to_exact_actual_sbom(field, value):
    row = artifact()
    match = copy.deepcopy(row)
    match[field] = value
    with pytest.raises(ValueError, match='artifact differs'):
        gate.bind_matches({'matches': [{'artifact': match}]}, {'artifacts': [row]})


def test_duplicate_full_sbom_identity_is_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        gate.sbom_inventory({'artifacts': [artifact(), artifact()]})


@pytest.fixture
def bridge(tmp_path):
    profile = {'go_source_bridge': {}}
    for name in ('mcli', 'silo'):
        old = {'GoVersion': 'go1.27.4', 'Path': name, 'Main': {'Path': name},
               'Settings': [{'Key': 'CGO_ENABLED', 'Value': '0'}], 'Deps': []}
        if name == 'silo':
            old['Deps'] = [{'Path': 'github.com/apache/thrift', 'Version': 'v0.24.0', 'Sum': 'prior'}]
        new = copy.deepcopy(old)
        if name == 'silo':
            new['Deps'][0] = {'Path': 'github.com/apache/thrift', 'Version': 'v0.25.0',
                              'Sum': 'h1:6LLlvoPjy8Cc/0b5TYRcTR3RZa2W0at3WQRcOkDwVGs='}
        paths = {}
        for key, value in [('native', new), ('prior_native', old)]:
            filename = name + '-' + key + '.json'
            (tmp_path / filename).write_text(json.dumps(value))
            paths[key] = filename
        for key in ('graph', 'prior_graph'):
            filename = name + '-' + key + '.gz'
            graph = [{'ImportPath': name}]
            if name == 'silo':
                graph += [{'ImportPath': 'github.com/apache/thrift/lib/go/thrift',
                           'Imports': ['testing'] if key == 'prior_graph' else [],
                           'Module': {'Path': 'github.com/apache/thrift',
                                      'Version': 'v0.24.0' if key == 'prior_graph' else 'v0.25.0'}},
                          {'ImportPath': 'testing', 'Standard': True},
                          {'ImportPath': 'github.com/minio/minio/internal/s3select/csv', 'GoFiles': ['reader.go']}]
            (tmp_path / filename).write_bytes(gzip.compress(''.join(json.dumps(row) for row in graph).encode()))
            paths[key] = filename
        profile['go_source_bridge'][name] = paths
    return profile, tmp_path


def test_go_bridge_verifies_actual_new_thrift_without_repeating_abnormal_probe(bridge):
    profile, root = bridge
    facts = gate.go_bridge(profile, root)
    assert facts['silo']['changed_modules'] == ['github.com/apache/thrift']
    assert facts['mcli']['changed_modules'] == []
    assert facts['silo']['abnormal_csv_probe_rerun'] is False


@pytest.mark.parametrize('field,value', [('GoVersion', 'go1.27.3'), ('Path', 'different'),
                                        ('Settings', []), ('Deps', [])])
def test_native_bridge_rejects_compiler_source_or_module_drift(bridge, field, value):
    profile, root = bridge
    path = root / profile['go_source_bridge']['silo']['native']
    row = json.loads(path.read_bytes())
    row[field] = value
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError):
        gate.go_bridge(profile, root)


@pytest.mark.parametrize('package', ['golang.org/x/net/html', 'golang.org/x/net/html/atom',
                                    'golang.org/x/crypto/openpgp/packet'])
def test_affected_package_entry_is_not_covered_by_historical_scope(bridge, package):
    profile, root = bridge
    for key in ('graph', 'prior_graph'):
        path = root / profile['go_source_bridge']['silo'][key]
        path.write_bytes(gzip.compress(gzip.decompress(path.read_bytes()) + json.dumps({'ImportPath': package}).encode()))
    with pytest.raises(ValueError, match='HTML/OpenPGP'):
        gate.go_bridge(profile, root)


def test_builder_has_separate_observation_operation_and_all_six_native_targets():
    import ast
    source = (Path(__file__).resolve().parents[1] / 'scripts/build_minio_os_20261113.py').read_text()
    tree = ast.parse(source)
    strings = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    assert {'verify-existing-image', 'build-and-verify', 'build_executed', 'observed_at_utc'} <= strings
    assert 'built_at_utc' not in strings
    assert {'usr/bin/curl', 'usr/bin/silo', 'usr/bin/mcli', 'usr/lib64/libc.so.6',
            'usr/lib64/ld-linux-x86-64.so.2', 'usr/lib64/libtic.so.6.2'} <= strings
    assert 'TestPinnedCVE202639414StopsUnterminatedRecord' not in source


@pytest.fixture
def os_matrix(tmp_path):
    old = [{'VulnerabilityID': 'CVE-' + str(index), 'PkgName': 'pkg' + str(index),
            'InstalledVersion': '1', 'Severity': 'MEDIUM', 'full_original_field': 'bound'} for index in range(3)]
    raw = {'Results': [{'Target': 'original-target', 'Class': 'os-pkgs', 'Vulnerabilities': old}]}
    (tmp_path / 'original.gz').write_bytes(gzip.compress(json.dumps(raw).encode()))
    rows = [{'original_target': 'original-target', 'original_finding': row,
             'resolution': 'signed_vendor_update' if index == 0 else 'verified_source_fix',
             'current_installed_version': '2'} for index, row in enumerate(old)]
    profile = {'os_resolution': rows, 'reviewed_findings': {'Trivy': [
        {'target': 'os:redhat', 'id': 'CVE-' + str(index), 'package': 'pkg' + str(index),
         'version': '2', 'severity': 'MEDIUM'} for index in (1, 2)]}}
    matrix = {'primary_sources': {'original_raw': {'path': 'original.gz'}}}
    return profile, matrix, tmp_path


def test_os_matrix_binds_original_full_findings_and_current_retained_rows(os_matrix):
    gate.verify_os_matrix(*os_matrix)


@pytest.mark.parametrize('change', ['replace', 'duplicate', 'original_field', 'missing_retained',
                                   'changed_severity', 'vendor_still_reported', 'new_os_row'])
def test_os_matrix_rejects_same_count_substitution_or_raw_drift(os_matrix, change):
    profile, matrix, root = os_matrix
    rows = profile['os_resolution']
    current = profile['reviewed_findings']['Trivy']
    if change == 'replace':
        rows[0]['original_finding']['VulnerabilityID'] = 'CVE-other'
    elif change == 'duplicate':
        rows[0] = copy.deepcopy(rows[1])
    elif change == 'original_field':
        rows[0]['original_finding']['full_original_field'] = 'different'
    elif change == 'missing_retained':
        current.pop()
    elif change == 'changed_severity':
        current[0]['severity'] = 'LOW'
    elif change == 'vendor_still_reported':
        current.append({'target': 'os:redhat', 'id': 'CVE-0', 'package': 'pkg0', 'version': '2', 'severity': 'MEDIUM'})
    else:
        current.append({'target': 'os:redhat', 'id': 'CVE-new', 'package': 'extra', 'version': '1', 'severity': 'LOW'})
    with pytest.raises(ValueError):
        gate.verify_os_matrix(profile, matrix, root)


@pytest.fixture
def actual_controls():
    path = Path(__file__).parent / 'fixtures/minio-native-20261113/scanner-controls.json'
    return json.loads(path.read_bytes())


def test_actual_default_kernel_rules_have_no_matching_package(actual_controls):
    assert gate.scanner_controls(actual_controls['grype'], actual_controls['sbom'])['matching_kernel_header_packages'] == 0


@pytest.mark.parametrize('key,value', [('only-fixed', True), ('only-notfixed', True),
                                      ('ignore-wontfix', True), ('vex-documents', ['other.json']),
                                      ('exclude', ['/usr/bin/curl'])])
def test_scanner_selection_cannot_filter_findings(actual_controls, key, value):
    actual_controls['grype']['descriptor']['configuration'][key] = value
    with pytest.raises(ValueError, match='suppression'):
        gate.scanner_controls(actual_controls['grype'], actual_controls['sbom'])


def test_default_rules_cannot_be_reused_when_actual_header_package_exists(actual_controls):
    actual_controls['sbom']['artifacts'].append({'name': 'kernel-headers', 'type': 'rpm'})
    with pytest.raises(ValueError, match='unused kernel-header'):
        gate.scanner_controls(actual_controls['grype'], actual_controls['sbom'])


def test_extra_ignore_rule_is_not_an_official_default(actual_controls):
    settings = actual_controls['grype']['descriptor']['configuration']
    settings['ignore'].append(copy.deepcopy(settings['ignore'][0]))
    with pytest.raises(ValueError, match='four built-in'):
        gate.scanner_controls(actual_controls['grype'], actual_controls['sbom'])


def test_reduced_catalogers_are_not_a_full_native_scan(actual_controls):
    actual_controls['sbom']['descriptor']['configuration']['catalogers']['requested'] = {'default': ['java']}
    with pytest.raises(ValueError, match='default Syft'):
        gate.scanner_controls(actual_controls['grype'], actual_controls['sbom'])
