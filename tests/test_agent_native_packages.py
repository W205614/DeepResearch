"""Boundary and counterexample tests for real fixed-library facts; no provider calls."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

import pytest
import yaml

from scripts import check_agent_native_packages as gate

NOW = datetime(2026, 11, 5, 8, tzinfo=timezone.utc)


@pytest.fixture
def inputs():
    lock = gate.load_lock()
    facts = {'image_id': 'sha256:' + 'a' * 64, 'diff_ids': ['sha256:' + 'b' * 64],
             'packages': {p['name']: {'library_sha256': p['library_sha256'],
                                    'loaded_library_sha256': p['library_sha256']} for p in lock['packages']}}
    packages, findings = [], []
    for p in lock['packages']:
        epoch, version = p['version'].split(':') if ':' in p['version'] else (None, p['version'])
        version, release = version.rsplit('-', 1)
        packages.append({'Name': p['name'], 'Version': version, 'Release': release,
                         **({'Epoch': int(epoch)} if epoch else {})})
        findings.append({'VulnerabilityID': p['fixed_advisories'][0], 'PkgName': p['name'],
                         'InstalledVersion': p['version'], 'Severity': 'HIGH', 'DataSource': {'ID': 'debian'}})
    packages += [{'Name': 'os-package-' + str(i), 'Version': '1'} for i in range(19)]
    report = {'SchemaVersion': 2, 'Trivy': {'Version': '0.75.0'}, 'ArtifactType': 'container_image',
              'CreatedAt': NOW.isoformat(), 'Metadata': {'ImageID': facts['image_id'],
              'DiffIDs': facts['diff_ids'], 'OS': {'Family': 'debian', 'Name': '13.7'}},
              'Results': [{'Target': 'agent', 'Class': 'os-pkgs', 'Type': 'debian',
                           'Packages': packages, 'Vulnerabilities': findings},
                          {'Target': 'Python', 'Class': 'lang-pkgs', 'Type': 'python-pkg',
                           'Packages': [{'Name': 'py-' + str(i), 'Version': '1'} for i in range(100)]}]}
    return report, facts, lock


def test_exact_official_fixed_bytes_preserve_complete_raw_report(inputs):
    before = deepcopy(inputs)
    result = gate.evaluate(*inputs, now=NOW)
    assert result['passed'] and len(result['verified_fixed']) == 2
    assert result['raw_counts'] == {'HIGH': 2} and result['raw_reports_modified'] is False
    assert inputs == before


@pytest.mark.parametrize('attack', ['new-cve', 'new-package', 'version', 'critical', 'other-source'])
def test_any_unreviewed_high_or_critical_still_blocks(inputs, attack):
    report, _, _ = inputs
    finding = report['Results'][0]['Vulnerabilities'][0]
    if attack == 'new-cve':
        finding['VulnerabilityID'] = 'CVE-UNREVIEWED'
    elif attack == 'new-package':
        finding['PkgName'], finding['InstalledVersion'] = 'os-package-0', '1'
    elif attack == 'version':
        finding['InstalledVersion'] = '3.54.0-1'
        report['Results'][0]['Packages'][0].update(Version='3.54.0', Release='1')
    elif attack == 'critical':
        finding['Severity'] = 'CRITICAL'
    else:
        finding['DataSource']['ID'] = 'nvd'
    result = gate.evaluate(*inputs, now=NOW)
    assert not result['passed'] and result['unresolved']


@pytest.mark.parametrize('attack', ['elf', 'soname', 'image', 'layers', 'inventory', 'python',
                                  'secret', 'secret-type', 'error', 'filtered', 'filtered-type',
                                  'duplicate', 'stale', 'future'])
def test_missing_changed_or_incomplete_proof_cannot_pass(inputs, attack):
    report, facts, lock = inputs
    os_record = report['Results'][0]
    if attack in {'elf', 'soname'}:
        facts['packages'][lock['packages'][0]['name']][
            'library_sha256' if attack == 'elf' else 'loaded_library_sha256'] = '0' * 64
    elif attack == 'image':
        report['Metadata']['ImageID'] = 'sha256:' + '0' * 64
    elif attack == 'layers':
        report['Metadata']['DiffIDs'] = []
    elif attack == 'inventory':
        os_record['Packages'].pop()
    elif attack == 'python':
        report['Results'].pop()
    elif attack == 'secret':
        os_record['Secrets'] = [{'Severity': 'LOW', 'Match': 'never-print-this'}]
    elif attack == 'secret-type':
        os_record['Secrets'] = ''
    elif attack == 'error':
        os_record['Errors'] = ['scan failed']
    elif attack == 'filtered':
        report['SuppressedFindings'] = [{}]
    elif attack == 'filtered-type':
        report['SuppressedFindings'] = ''
    elif attack == 'duplicate':
        os_record['Vulnerabilities'].append(deepcopy(os_record['Vulnerabilities'][0]))
    else:
        report['CreatedAt'] = (NOW + timedelta(days=2 if attack == 'future' else -2)).isoformat()
    with pytest.raises(ValueError) as error:
        gate.evaluate(*inputs, now=NOW)
    assert 'never-print-this' not in str(error.value)


def test_original_nonblocking_severities_are_retained(inputs):
    row = inputs[0]['Results'][0]['Vulnerabilities'][0]
    row['Severity'] = 'UNKNOWN'
    result = gate.evaluate(*inputs, now=NOW)
    assert result['passed'] and result['raw_counts']['UNKNOWN'] == 1


def test_required_agent_workflow_keeps_complete_scan_and_final_gate():
    path = Path(__file__).parents[1] / '.github/workflows/security.yml'
    steps = yaml.safe_load(path.read_text(encoding='utf-8'))['jobs']['application-image-scan']['steps']
    full = [s for s in steps if 'trivy image' in s.get('run', '') and s.get('if') == "matrix.name == 'agent'"]
    final = [s for s in steps if 'scripts/check_agent_native_packages.py' in s.get('run', '')]
    assert len(full) == len(final) == 1 and steps.index(full[0]) < steps.index(final[0])
    assert '--list-all-pkgs' in full[0]['run'] and '--scanners vuln,secret' in full[0]['run']
    assert '--severity UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL' in full[0]['run']
    assert final[0].get('if') == "matrix.name == 'agent'" and not final[0].get('continue-on-error')
    assert not any(s.get('continue-on-error') for s in full)
    json.dumps(gate.load_lock())
