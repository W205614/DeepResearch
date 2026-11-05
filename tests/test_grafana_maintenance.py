"""New subject, per-severity rows and authority cannot inherit old acceptance."""
from copy import deepcopy
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path

import pytest

from scripts import check_grafana_candidate_exact28 as old
from scripts import check_grafana_maintenance as gate
from scripts import fetch_grafana_candidate as fetch
from scripts import scan_grafana_candidate as collector

ROOT = Path(__file__).parents[1]
BASELINE_TRIVY = json.loads(gzip.decompress(
    (ROOT / 'deployment/security/grafana-maintenance-evidence/candidate-full-trivy.json.gz').read_bytes()))
NOW = datetime.fromisoformat(BASELINE_TRIVY['CreatedAt'].replace('Z', '+00:00')) + timedelta(hours=1)


def read(name):
    return json.loads((ROOT / name).read_bytes())


@pytest.fixture
def facts():
    folder = ROOT / 'deployment/security/grafana-maintenance-evidence'
    trivy, grype, sbom = [json.loads(gzip.decompress((folder / f'candidate-full-{tool}.json.gz').read_bytes()))
                         for tool in ('trivy', 'grype', 'syft')]
    prep = read('deployment/security/grafana-maintenance-preparation.json')
    binding = deepcopy(read('deployment/security/grafana-candidate-request.json')['binding'])
    binding.update(deepcopy(prep['subject']), validity='change-driven')
    binding['expected_native_application'] = {
        **binding['expected_native_application'], 'version': '13.2.5',
        'cpe': binding['expected_native_application']['cpe'].replace('13.2.4', '13.2.5')}
    trivy_rows = deepcopy(prep['remaining_blocking_rows_require_new_subject_review']['Trivy'])
    native_rows = deepcopy(prep['remaining_blocking_rows_require_new_subject_review']['Grype'])
    aliases = []
    for row in native_rows:
        canonical = {'GHSA-p4r4-xvrq-gvmc': 'CVE-2026-21728',
                     'GHSA-ffqx-q65f-36jf': 'CVE-2026-28377',
                     'GO-2026-6860': 'CVE-2026-78661'}.get(row['id'])
        if canonical:
            aliases.append({**{k: v for k, v in row.items() if k != 'id'}, 'scanner': 'Grype',
                            'raw_id': row['id'], 'canonical_id': canonical})
    allowed = deepcopy(trivy_rows)
    allowed.extend({**row, 'id': 'CVE-2026-78661'} for row in native_rows if row['id'] == 'GO-2026-6860')
    for row in allowed:
        row['disposition'] = 'FIXED' if row['id'] in {'CVE-2026-21728', 'CVE-2026-28377'} else 'NOT_AFFECTED'
    inventory = {(r['Type'] if r.get('Class') == 'os-pkgs' else r['Target']):
                 [{'name': p['Name'], 'version': p.get('Version')} for p in r['Packages']]
                 for r in trivy['Results']}
    binding.update(allowed_findings=allowed, exact_trivy_rows=trivy_rows, exact_grype_rows=native_rows,
                   scanner_exact_aliases=aliases, exact_trivy_inventory=inventory,
                   exact_trivy_coverage={k: len(v) for k, v in inventory.items()})
    binding['minimum_trivy_coverage'] = binding['exact_trivy_coverage']
    binding['exact_syft_types'] = {'npm': 4, 'apk': 33, 'go-module': 1720, 'binary': 1}
    binding['minimum_syft_types'] = binding['exact_syft_types']
    for row in binding['inventory_identity']['local_source_modules']:
        row['elf_sha256'] = binding['binary_sha256'][row['target']]
    inspection = read('deployment/security/grafana-maintenance-evidence/subject.json')
    inspection = {'Id': inspection['index_digest'], 'RootFS': {'Layers': inspection['diff_ids']},
                  'Architecture': 'amd64', 'Os': 'linux'}
    request = {'binding': binding, 'validity': 'change-driven', 'expires': binding['expires']}
    return trivy, grype, sbom, inspection, request


def verify(facts):
    return gate.verify_full_scans(*facts[:4], facts[4]['binding'], trivy_exit_code=0,
                                 grype_exit_code=0, now=NOW)


def auth(request):
    return {'accepted': True, 'request_sha256': gate.REQUEST_SHA256,
            'subject': gate.subject_for(request['binding']),
            'exact_target_advisory_rows': deepcopy(request['binding']['allowed_findings']),
            'policy': {**dict.fromkeys(gate.POLICY_FLAGS, True), 'validity': 'change-driven',
                       'expires': request['binding']['expires']}}


def test_actual_complete_reports_match_separate_high_scope_without_changing_raw(facts):
    before = deepcopy(facts)
    gate.validate_contract(facts[4]['binding'])
    result = verify(facts)
    assert result['passed'] and result['unresolved_count'] == 0
    assert len(result['verified_dispositions']['Trivy']) == 28
    assert len(result['verified_dispositions']['Grype']) == 12
    assert facts == before and result['raw_reports_modified'] is False
    assert len(facts[4]['binding']['allowed_findings']) == 33
    with pytest.raises(ValueError):
        old.validate_contract(facts[4]['binding'])


@pytest.mark.parametrize('change', ['missing', 'extra', 'duplicate', 'severity', 'id', 'target', 'version'])
def test_each_new_native_high_tuple_is_exact(facts, change):
    rows = facts[1]['matches']
    row = next(r for r in rows if r['vulnerability']['id'] == 'GO-2026-6860')
    if change == 'missing':
        rows.remove(row)
    elif change == 'extra':
        rows.append({**deepcopy(row), 'vulnerability': {'id': 'UNREVIEWED', 'severity': 'High'}})
    elif change == 'duplicate':
        rows.append(deepcopy(row))
    elif change in {'severity', 'id'}:
        row['vulnerability'][change] = 'Unknown' if change == 'severity' else 'CVE-UNREVIEWED'
    elif change == 'target':
        row['artifact']['locations'][0]['path'] = '/usr/bin/unreviewed'
    else:
        row['artifact']['version'] = 'v0.59.0'
    with pytest.raises(ValueError):
        verify(facts)


@pytest.mark.parametrize('change', ['scope', 'downgrade', 'alias', 'target', 'policy'])
def test_request_cannot_drop_high_rows_or_make_a_broad_alias(facts, change):
    binding = facts[4]['binding']
    if change == 'scope':
        binding['allowed_findings'].pop()
    elif change == 'downgrade':
        binding['allowed_findings'][-1]['severity'] = 'UNKNOWN'
    elif change == 'alias':
        binding['scanner_exact_aliases'][-1]['canonical_id'] = 'CVE-2026-97045'
    elif change == 'target':
        binding['scanner_exact_aliases'][-1]['target'] = 'usr/bin/new-target'
    else:
        binding['validity'] = 'unconditional'
    with pytest.raises(ValueError):
        gate.validate_contract(binding)


@pytest.mark.parametrize('change', ['trivy', 'grype', 'database', 'future', 'scanner'])
def test_scan_and_database_freshness_remain_required(facts, change):
    old_stamp = (NOW - timedelta(days=8)).isoformat()
    if change == 'trivy':
        facts[0]['CreatedAt'] = old_stamp
    elif change == 'grype':
        facts[1]['descriptor']['timestamp'] = old_stamp
    elif change == 'database':
        facts[1]['descriptor']['db']['status']['built'] = old_stamp
    elif change == 'future':
        facts[0]['CreatedAt'] = (NOW + timedelta(hours=1)).isoformat()
    else:
        facts[1]['descriptor']['version'] = 'unreviewed'
    with pytest.raises(ValueError):
        verify(facts)


def test_old_authorization_cannot_accept_new_subject_or_extra_per_severity_scope(facts, monkeypatch):
    monkeypatch.setattr(gate, 'REQUEST_SHA256', 'e' * 64)
    request = facts[4]
    new = auth(request)
    gate.validate_authorization(new, request)
    with pytest.raises(ValueError):
        gate.validate_authorization(read('deployment/security/grafana-candidate-authorization.json'), request)
    missing = deepcopy(new)
    missing['exact_target_advisory_rows'].pop()
    with pytest.raises(ValueError):
        gate.validate_authorization(missing, request)
    assert gate.final_result({'passed': True}, request, new)['passed']
    assert gate.final_result({'passed': True}, request, new, prepare=True)['passed'] is False


def test_unset_request_pin_blocks_before_any_file_access(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'REQUEST_SHA256', None)
    with pytest.raises(ValueError, match='pin is not set'):
        gate.load_request(tmp_path)


def test_new_transport_cannot_inherit_old_request_or_release(facts, monkeypatch):
    monkeypatch.setattr(gate, 'REQUEST_SHA256', 'e' * 64)
    monkeypatch.setattr(gate, 'TRANSPORT_SHA256', None)
    profile = fetch.maintenance_profile()
    with pytest.raises(ValueError):
        fetch.validate_transport(read('deployment/security/grafana-candidate-transport.json'),
                                 facts[4]['binding'], profile=profile)
    with pytest.raises(ValueError, match='pin is not set'):
        fetch.load_transport(ROOT, facts[4]['binding'], profile=profile)


def test_maintenance_collector_checks_new_authority_before_operations(tmp_path, monkeypatch):
    def missing(_root):
        raise ValueError('New exact maintenance authority is missing')
    monkeypatch.setattr(gate, 'load_request', missing)
    monkeypatch.setattr(collector, 'run', lambda *_a, **_k: pytest.fail('operation before new authority'))
    result = collector.collect(type('Args', (), {})(), consumer=gate)
    assert result['operational_success'] is False and result['artifact_approved'] is False


def test_loader_maintenance_cli_selects_only_the_new_authority(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, 'ROOT', tmp_path)
    called = []

    def missing(_root):
        called.append('maintenance')
        raise ValueError('New exact maintenance authority is missing')

    monkeypatch.setattr(gate, 'load_request', missing)
    monkeypatch.setattr(old, 'load_request', lambda *_a: pytest.fail('Old authority cannot be inherited'))
    monkeypatch.setattr(fetch.subprocess, 'run', lambda *_a, **_k: pytest.fail('Docker before new authority'))
    assert fetch.main(['--profile', 'maintenance']) == 1
    assert called == ['maintenance']
