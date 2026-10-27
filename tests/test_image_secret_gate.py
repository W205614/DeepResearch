from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from scripts.check_image_secrets import assess


def report():
    return {'SchemaVersion': 2, 'ArtifactType': 'container_image', 'Trivy': {'Version': '0.75.0'},
            'Results': [{'Target': 'usr/bin/component', 'Vulnerabilities': [{'Severity': 'HIGH'}]}]}


def test_no_secrets_does_not_claim_vulnerability_disposition():
    result = assess(report())
    assert result['passed'] and result['secret_findings'] == 0
    assert 'vulnerability_and_identity_gates_still_required' in result['mode']


@pytest.mark.parametrize('severity', ['UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'])
def test_any_secret_blocks_without_echoing_matched_material(severity):
    scan = report()
    scan['Results'][0]['Secrets'] = [{'RuleID': 'private-key', 'Severity': severity,
                                     'Match': 'DO_NOT_PRINT_MATCH', 'Code': {'Lines': ['DO_NOT_PRINT_EXCERPT']}}]
    result = assess(scan)
    assert not result['passed'] and result['secret_findings'] == 1
    assert 'DO_NOT_PRINT' not in json.dumps(result)


def test_failed_or_missing_scan_cannot_look_like_zero():
    with pytest.raises(ValueError):
        assess(report(), 1)
    missing = deepcopy(report())
    missing['Results'] = []
    with pytest.raises(ValueError):
        assess(missing)
    malformed = report()
    malformed['Results'][0]['Secrets'] = 'not-an-inventory'
    with pytest.raises(ValueError):
        assess(malformed)


@pytest.mark.parametrize('inventory', [None, 0, False, {}, '', 'not-an-inventory'])
def test_falsey_non_list_secret_inventory_is_not_zero(inventory):
    scan = report()
    scan['Results'][0]['Secrets'] = inventory
    with pytest.raises(ValueError):
        assess(scan)


@pytest.mark.parametrize('field', ['Errors', 'ModifiedFindings', 'SuppressedFindings'])
def test_failed_or_filtered_target_blocks(field):
    scan = report()
    scan['Results'][0][field] = ['incomplete']
    with pytest.raises(ValueError):
        assess(scan)


@pytest.mark.parametrize('malformed', [None, False, 0, {}])
def test_falsey_failure_or_suppression_metadata_cannot_be_treated_as_absent(malformed):
    for field in ('Errors', 'ModifiedFindings', 'SuppressedFindings'):
        for target in ('report', 'result'):
            scan = report()
            selected = scan if target == 'report' else scan['Results'][0]
            selected[field] = malformed
            with pytest.raises(ValueError):
                assess(scan)


def test_malformed_scanner_metadata_blocks():
    scan = report()
    scan['Trivy'] = None
    with pytest.raises(ValueError):
        assess(scan)


def test_approved_workflow_scans_secrets_before_vulnerability_dispositions():
    workflow = yaml.safe_load((Path(__file__).parents[1]/'.github/workflows/security.yml').read_text(encoding='utf-8'))
    steps = workflow['jobs']['runtime-image-scan']['steps']
    scan_index = next(i for i, step in enumerate(steps) if step.get('id') == 'approved_scan')
    gate_index = next(i for i, step in enumerate(steps) if 'scripts/check_image_secrets.py' in step.get('run', ''))
    disposition_index = next(i for i, step in enumerate(steps) if 'scripts/image_disposition.py' in step.get('run', ''))
    assert steps[scan_index]['with']['scanners'] == 'vuln,secret'
    assert scan_index < gate_index < disposition_index
    assert not steps[gate_index].get('continue-on-error', False)
    artifact_names = [step['with']['name'] for job in workflow['jobs'].values()
                      for step in job['steps'] if step.get('uses', '').startswith('actions/upload-artifact@')]
    assert len(artifact_names) == len(set(artifact_names))
