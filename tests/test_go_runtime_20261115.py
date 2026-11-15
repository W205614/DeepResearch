from copy import deepcopy

import pytest

from scripts import check_go_runtime_20261115 as gate


@pytest.fixture
def compiled_context():
    module = {'Path': 'example.test/app', 'Version': '(devel)'}
    native = {'Path': 'example.test/app', 'Main': module, 'GoVersion': 'go1.27.4',
              'Deps': [{'Path': 'golang.org/x/crypto', 'Version': 'v0.62.0', 'Sum': 'h1:exact'}],
              'Settings': [{'Key': key, 'Value': value} for key, value in
                           {'GOOS': 'linux', 'GOARCH': 'amd64', 'CGO_ENABLED': '0'}.items()]}
    packages = {'example.test/app': {'Name': 'main', 'Module': {'Path': module['Path'], 'Main': True},
                                     'Imports': ['golang.org/x/crypto/ssh']},
                'golang.org/x/crypto/ssh': {'Name': 'ssh', 'DepOnly': True,
                                           'Module': deepcopy(native['Deps'][0])}}
    receipt = {'compiler': 'go version go1.27.4 windows/amd64',
               'environment': {'GOOS': 'linux', 'GOARCH': 'amd64', 'CGO_ENABLED': '0'},
               'command': ['go', 'list', '-deps', '-json', '-mod=readonly', '.']}
    return native, receipt, packages


def test_cross_host_graph_matches_actual_target_compilation(compiled_context):
    gate.verify_graph(*compiled_context)


def test_official_go_json_can_omit_an_empty_cgo_flag_value(compiled_context):
    native, receipt, packages = compiled_context
    native['Settings'].append({'Key': 'CGO_CFLAGS'})
    gate.verify_graph(native, receipt, packages)


def test_module_free_go_file_build_has_no_synthetic_main_module(compiled_context):
    native, receipt, packages = compiled_context
    previous = native['Path']
    native['Deps'].append(native['Main'])
    native['Main'] = {}
    native['Path'] = 'command-line-arguments'
    packages[previous]['Name'] = 'library'
    packages[native['Path']] = {'Name': 'main', 'Imports': [previous]}
    receipt['command'][-1] = 'cmd/main.go'
    gate.verify_graph(native, receipt, packages)
    receipt['command'][-1] = '.'
    with pytest.raises(ValueError, match='module-free entry'):
        gate.verify_graph(native, receipt, packages)


def test_only_actual_cgo_import_admits_implicit_runtime_cgo(compiled_context):
    native, receipt, packages = compiled_context
    receipt['environment']['CGO_ENABLED'] = '1'
    next(row for row in native['Settings'] if row['Key'] == 'CGO_ENABLED')['Value'] = '1'
    packages[native['Path']]['Imports'].append('C')
    packages['runtime/cgo'] = {'Name': 'cgo', 'Standard': True}
    gate.verify_graph(native, receipt, packages)
    packages[native['Path']]['Imports'].remove('C')
    with pytest.raises(ValueError, match='outside the actual production'):
        gate.verify_graph(native, receipt, packages)


@pytest.mark.parametrize('change', ['not-main', 'dependency-entry', 'test-entry', 'disconnected',
                                   'duplicate-native', 'wrong-main-module'])
def test_entry_identity_and_its_complete_reachable_graph_are_required(compiled_context, change):
    native, receipt, packages = deepcopy(compiled_context)
    entry = packages[native['Path']]
    if change == 'not-main':
        entry['Name'] = 'library'
    elif change == 'dependency-entry':
        entry['DepOnly'] = True
    elif change == 'test-entry':
        entry['ForTest'] = native['Path']
    elif change == 'disconnected':
        packages['not/reachable'] = {'Name': 'unused', 'DepOnly': True, 'Standard': True}
    elif change == 'wrong-main-module':
        entry['Module']['Path'] = native['Deps'][0]['Path']
    else:
        native['Deps'].append(deepcopy(native['Main']))
    with pytest.raises(ValueError):
        gate.verify_graph(native, receipt, packages)


@pytest.mark.parametrize('version', ['v1.2.3+dirty', 'v0.0.0-20261115000000-' + 'b' * 12 + '+dirty'])
def test_real_main_vcs_version_is_bound_without_mutating_the_graph(compiled_context, version):
    native, receipt, packages = compiled_context
    native['Main']['Version'] = version
    native['Settings'].append({'Key': 'vcs.revision', 'Value': 'b' * 40})
    receipt['source_provenance'] = {'Version': 'v1.2.3', 'Origin': {'Hash': 'b' * 40}}
    before = deepcopy(packages)
    gate.verify_graph(native, receipt, packages)
    assert packages == before
    native['Main']['Version'] = 'v99.0.0'
    with pytest.raises(ValueError, match='neither the verified source version'):
        gate.verify_graph(native, receipt, packages)
    native['Main']['Version'] = version
    native['Settings'][-1]['Value'] = 'c' * 40
    with pytest.raises(ValueError, match='VCS stamp'):
        gate.verify_graph(native, receipt, packages)


@pytest.mark.parametrize('change', ['compiler', 'target', 'tags', 'module', 'entry', 'missing-module'])
def test_actual_elf_cannot_inherit_another_source_context(compiled_context, change):
    native, receipt, packages = deepcopy(compiled_context)
    if change == 'compiler':
        native['GoVersion'] = 'go1.27.3'
    elif change == 'target':
        receipt['environment']['GOOS'] = 'windows'
    elif change == 'tags':
        receipt['command'].insert(-1, '-tags=another')
    elif change == 'module':
        native['Deps'][0]['Sum'] = 'h1:unreviewed'
    elif change == 'entry':
        native['Path'] = 'example.test/other'
    else:
        native['Deps'] = []
    with pytest.raises(ValueError):
        gate.verify_graph(native, receipt, packages)


def test_only_exact_uncompiled_openpgp_finding_is_adjudicated(compiled_context):
    native, _, packages = compiled_context
    finding = {'VulnerabilityID': 'GO-2026-5932', 'PkgName': 'golang.org/x/crypto',
               'InstalledVersion': 'v0.62.0', 'Severity': 'UNKNOWN'}
    scan = {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Trivy': {'Version': '0.75.0'},
            'Results': [{'Target': 'bin/tool', 'Vulnerabilities': [finding]}]}
    assert gate.evaluate_findings(scan, {'bin/tool': (packages, native)})['passed']
    packages['golang.org/x/crypto/openpgp/packet'] = {}
    with pytest.raises(ValueError, match='OpenPGP package enters'):
        gate.evaluate_findings(scan, {'bin/tool': (packages, native)})


def test_scanner_inventory_must_include_all_native_modules_and_stdlib(compiled_context):
    native, _, _ = compiled_context
    packages = [{'Name': native['Main']['Path']},
                {'Name': 'golang.org/x/crypto', 'Version': 'v0.62.0'},
                {'Name': 'stdlib', 'Version': 'v1.27.4'}]
    gate.verify_go_inventory({'Packages': packages}, native)
    for changed in (packages[:-1], packages + [packages[-1]],
                    packages[:-1] + [{'Name': 'stdlib', 'Version': 'v1.27.3'}]):
        with pytest.raises(ValueError, match='package inventory differs'):
            gate.verify_go_inventory({'Packages': changed}, native)


@pytest.mark.parametrize('severity', ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL', 'UNKNOWN'])
def test_every_unreviewed_severity_blocks(severity):
    scan = {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Trivy': {'Version': '0.75.0'},
            'Results': [{'Target': 'bin/tool', 'Vulnerabilities': [{
        'VulnerabilityID': 'NEW', 'PkgName': 'module', 'InstalledVersion': 'v1', 'Severity': severity}]}]}
    result = gate.evaluate_findings(scan, {})
    assert not result['passed'] and len(result['unresolved']) == 1


@pytest.mark.parametrize('identifier,severity,fixed', [
    ('CVE-2026-28377', 'HIGH', '2.10.3'),
    ('CVE-2026-27878', 'MEDIUM', '1.5.1-0.20260303204923-b13f74291d48'),
])
def test_tempo_supplement_requires_exact_finding(identifier, severity, fixed):
    version = 'v2.10.2+incompatible'
    native = {'Main': {'Path': 'github.com/grafana/tempo', 'Version': version}}
    finding = {'VulnerabilityID': identifier, 'Severity': severity, 'FixedVersion': fixed,
               'PkgName': native['Main']['Path'], 'InstalledVersion': version}
    scan = {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Trivy': {'Version': '0.75.0'},
            'Results': [{'Target': 'tempo', 'Vulnerabilities': [finding]}]}
    proof = {'actual_module_version': version}
    assert not gate.evaluate_findings(scan, {'tempo': ({}, native)})['passed']
    assert gate.evaluate_findings(scan, {'tempo': ({}, native)}, proof)['passed']
    for key in ('Severity', 'FixedVersion', 'PkgName', 'InstalledVersion'):
        changed = deepcopy(scan)
        changed['Results'][0]['Vulnerabilities'][0][key] = 'unreviewed'
        with pytest.raises(ValueError, match='exact Tempo finding changed'):
            gate.evaluate_findings(changed, {'tempo': ({}, native)}, proof)
