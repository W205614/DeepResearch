"""Exact fixed-source applicability cannot hide another blocker or change bytes."""
import copy
from datetime import datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts import check_tempo_app_exact1 as gate


@pytest.fixture
def facts():
    review = gate.load_review()
    folder = gate.ROOT / gate.EVIDENCE
    report = gate.strict_json(gzip.decompress((folder / 'reviewed-high-only-raw.json.gz').read_bytes()))
    inspection = gate.strict_json((folder / 'image-inspect.json').read_bytes())
    material = gate.strict_json((folder / 'materials.json').read_bytes())
    native = gate.strict_json((folder / 'native-buildinfo.json').read_bytes())
    return report, inspection, material, native, review


def assess(facts, **kwargs):
    return gate.assess(*facts, scanner_exit_code=kwargs.pop('scanner_exit_code', 0),
                       now=kwargs.pop('now', datetime.fromisoformat(facts[0]['CreatedAt'])), **kwargs)


def test_actual_bound_high_is_visible_and_inputs_remain_unchanged(facts):
    before = copy.deepcopy(facts)
    result = assess(facts)
    assert result['passed'] and result['original_gate']['passed'] is False
    assert result['raw_counts'] == {'HIGH': 1}
    assert len(result['technical_applied']) == 1
    assert result['technical_applied'][0]['elf_sha256'] == facts[2]['elf_sha256']
    assert result['raw_report_modified'] is False and facts == before
    assert result['human_risk_acceptance'] is result['oci_authorization'] is False


@pytest.mark.parametrize('change', ['id', 'package', 'version', 'severity', 'fixed_version',
                                   'layer', 'missing', 'duplicate', 'new_high', 'new_critical'])
def test_missing_duplicate_changed_or_another_blocker_fails(facts, change):
    finding = facts[0]['Results'][1]['Vulnerabilities'][0]
    rows = facts[0]['Results'][1]['Vulnerabilities']
    if change == 'missing':
        rows.clear()
    elif change == 'duplicate':
        rows.append(copy.deepcopy(finding))
    elif change in {'new_high', 'new_critical'}:
        rows.append({**finding, 'VulnerabilityID': 'NEW-UNREVIEWED',
                     'Severity': 'HIGH' if change == 'new_high' else 'CRITICAL'})
    elif change == 'layer':
        finding['Layer']['DiffID'] = facts[1]['RootFS']['Layers'][0]
    else:
        field = {'id': 'VulnerabilityID', 'package': 'PkgName', 'version': 'InstalledVersion',
                 'severity': 'Severity', 'fixed_version': 'FixedVersion'}[change]
        finding[field] = 'LOW' if change == 'severity' else 'CHANGED'
    with pytest.raises(ValueError):
        assess(facts)


@pytest.mark.parametrize('severity', ['LOW', 'MEDIUM', 'UNKNOWN'])
def test_original_threshold_does_not_become_a_zero_vulnerability_gate(facts, severity):
    rows = facts[0]['Results'][1]['Vulnerabilities']
    rows.append({**rows[0], 'VulnerabilityID': 'ANOTHER-NONBLOCKING', 'Severity': severity})
    result = assess(facts)
    assert result['passed'] and result['raw_counts'] == {'HIGH': 1, severity: 1}


@pytest.mark.parametrize('severity', ['LOW', 'MEDIUM', 'UNKNOWN', 'HIGH', 'CRITICAL'])
def test_every_secret_blocks_without_emitting_values(facts, severity):
    facts[0]['Results'][0]['Secrets'] = [{'RuleID': 'example', 'Severity': severity,
                                       'Match': 'MUST-NOT-BE-EMITTED'}]
    with pytest.raises(ValueError) as error:
        assess(facts)
    assert 'MUST-NOT-BE-EMITTED' not in str(error.value)


@pytest.mark.parametrize('field,value', [('Packages', {}), ('Vulnerabilities', False),
                                       ('Secrets', None), ('Errors', False),
                                       ('SuppressedFindings', [{}]), ('ModifiedFindings', [{}])])
def test_malformed_or_filtered_collections_are_not_empty_successes(facts, field, value):
    facts[0]['Results'][1][field] = value
    with pytest.raises(ValueError):
        assess(facts)


@pytest.mark.parametrize('change', ['scan_image', 'scan_diff_ids', 'material_image', 'material_diff_ids',
                                   'native_inventory', 'missing_package', 'duplicate_package',
                                   'package_layer', 'extra_target', 'duplicate_target', 'target_type'])
def test_fresh_scan_identity_and_complete_native_inventory_are_required(facts, change):
    report, inspection, material, native, _review = facts
    if change == 'scan_image':
        report['Metadata']['ImageID'] = 'sha256:' + 'f' * 64
    elif change == 'scan_diff_ids':
        report['Metadata']['DiffIDs'].pop()
    elif change == 'material_image':
        material['actual_image_id'] = 'sha256:' + 'e' * 64
    elif change == 'material_diff_ids':
        material['actual_diff_ids'] = list(reversed(material['actual_diff_ids']))
    elif change == 'native_inventory':
        native['Deps'][0]['Version'] = 'v999.0.0'
    elif change == 'missing_package':
        report['Results'][1]['Packages'].pop()
    elif change == 'duplicate_package':
        report['Results'][1]['Packages'].append(copy.deepcopy(report['Results'][1]['Packages'][0]))
    elif change == 'package_layer':
        report['Results'][1]['Packages'][0]['Layer'] = report['Results'][1]['Packages'][2]['Layer']
    elif change == 'extra_target':
        report['Results'].append({'Target': 'other', 'Packages': []})
    elif change == 'duplicate_target':
        report['Results'][0]['Target'] = 'tempo'
    else:
        report['Results'][1]['Type'] = 'other'
    with pytest.raises(ValueError):
        assess(facts)


def test_timestamp_rebuilt_image_must_cross_bind_all_actual_run_identities(facts):
    # The technical subject is the same ELF/source; this is not an inherited OCI approval.
    report, inspection, material, _native, _review = facts
    new = 'sha256:' + 'a' * 64
    report['Metadata']['ImageID'] = inspection['Id'] = material['actual_image_id'] = new
    assert assess(facts)['actual_image_id'] == new
    material['actual_image_id'] = 'sha256:' + 'b' * 64
    with pytest.raises(ValueError):
        assess(facts)


@pytest.mark.parametrize('code', [1, 2, -1])
def test_scanner_operational_failure_is_not_a_disposition(facts, code):
    with pytest.raises(ValueError):
        assess(facts, scanner_exit_code=code)


def test_stale_scan_fails(facts):
    with pytest.raises(ValueError):
        assess(facts, now=datetime.fromisoformat(facts[0]['CreatedAt']) + timedelta(days=2))


@pytest.fixture
def material_files(facts, tmp_path, monkeypatch):
    review = copy.deepcopy(facts[4])
    folder = gate.ROOT / gate.EVIDENCE
    for name in gate.MATERIAL_NAMES:
        if name != 'tempo.elf':
            (tmp_path / name).write_bytes((folder / name).read_bytes())
    elf = b'\x7fELFunit-test-only-the-native-parser-is-mocked'
    (tmp_path / 'tempo.elf').write_bytes(elf)
    review['scope']['elf_bytes'] = len(elf)
    review['scope']['elf_sha256'] = hashlib.sha256(elf).hexdigest()
    material = gate.strict_json((tmp_path / 'materials.json').read_bytes())
    material.update(elf_bytes=len(elf), elf_sha256=review['scope']['elf_sha256'])
    (tmp_path / 'materials.json').write_text(json.dumps(material), encoding='utf-8')

    def native_read(args, **_kwargs):
        return (tmp_path / ('native-buildinfo.json' if '-json' in args
                            else 'native-buildinfo.txt')).read_bytes()

    monkeypatch.setattr(gate.subprocess, 'check_output', native_read)
    return tmp_path, review


def test_material_verification_requires_the_actual_native_parser_result(material_files, monkeypatch):
    directory, review = material_files
    assert gate.verify_materials(directory, review)[2]['GoVersion'] == 'go1.27.3'
    monkeypatch.setattr(gate.subprocess, 'check_output', lambda *_a, **_k: b'{}')
    with pytest.raises(ValueError):
        gate.verify_materials(directory, review)


@pytest.mark.parametrize('change', ['elf', 'native', 'version', 'config', 'image', 'base_layer', 'approval'])
def test_elf_native_recipe_runtime_scope_cannot_drift(material_files, change):
    directory, review = material_files
    if change in {'elf', 'native', 'version'}:
        name = {'elf': 'tempo.elf', 'native': 'native-buildinfo.json', 'version': 'runtime-version.txt'}[change]
        path = directory / name
        path.write_bytes(path.read_bytes() + b' ')
    else:
        path = directory / ('materials.json' if change == 'approval' else 'image-inspect.json')
        data = gate.strict_json(path.read_bytes())
        if change == 'config':
            data['Config']['Entrypoint'] = ['/unreviewed']
        elif change == 'image':
            data['Id'] = 'sha256:' + '0' * 64
        elif change == 'base_layer':
            data['RootFS']['Layers'][0] = 'sha256:' + '1' * 64
        else:
            data['security_gate_claim'] = True
        path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        gate.verify_materials(directory, review)


def test_changed_review_cannot_apply_a_fact(tmp_path, monkeypatch):
    raw = (gate.ROOT / gate.REVIEW_PATH).read_bytes()
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    path = tmp_path / gate.REVIEW_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(raw + b' ')
    with pytest.raises(ValueError):
        gate.load_review()


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}'])
def test_ambiguous_json_is_not_evidence(raw):
    with pytest.raises(ValueError):
        gate.strict_json(raw)


@pytest.mark.parametrize('alias', ['scan', 'materials', 'existing_link', 'outside_cache'])
def test_output_never_overwrites_input_or_evidence(tmp_path, monkeypatch, alias):
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    cache = tmp_path / '.cache/eval'
    cache.mkdir(parents=True)
    scan = cache / 'raw.json'
    scan.write_bytes(b'ORIGINAL')
    materials = cache / 'materials'
    materials.mkdir()
    existing = materials / 'materials.json'
    existing.write_bytes(b'PROTECTED')
    if alias == 'scan':
        output = scan
    elif alias == 'materials':
        output = existing
    elif alias == 'outside_cache':
        output = tmp_path / 'other.json'
    else:
        output = cache / 'linked.json'
        output.hardlink_to(scan)
    assert gate.main(['--scan', str(scan), '--materials-dir', str(materials),
                      '--scanner-exit-code', '0', '--output', str(output)]) == 1
    assert scan.read_bytes() == b'ORIGINAL' and existing.read_bytes() == b'PROTECTED'


def test_review_evidence_and_recipe_are_currently_pinned():
    review = gate.load_review()
    assert review['review_sample']['native_rederived_from_actual_elf'] is True
    assert review['human_risk_acceptance'] is False
    assert Path('deployment/docker/Dockerfile.tempo').as_posix() in review['fixed_subject']['input_sha256']
