"""Exact inventory migration counterexamples; original findings and proofs remain required."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import check_grafana_source_scan_20261112 as gate

ROOT = Path(__file__).resolve().parents[1]
TARGET = 'business-backend/pom.xml'
TOMCAT27_SOURCE = '28ae8767b18a4c4d8329cb5c38f3e0fa5d310e55'


@pytest.fixture(scope='module')
def reviewed_root(tmp_path_factory):
    root = tmp_path_factory.mktemp('tomcat27-review')
    proof = json.loads((ROOT / gate.SOURCE_REVIEW).read_bytes())
    for path in {gate.SOURCE_REVIEW, gate.prior_build.PROFILE_PATH, *proof['evidence_sha256']}:
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, destination)
    pom = root / TARGET
    pom.parent.mkdir(parents=True, exist_ok=True)
    pom.write_bytes(subprocess.check_output(['git', 'show', f'{TOMCAT27_SOURCE}:{TARGET}'], cwd=ROOT))
    return root


@pytest.fixture
def review_inputs(reviewed_root):
    # These complete inventories/identities were generated from the two real CI
    # artifacts. The full unmodified reports are replayed separately.
    historical = json.loads((reviewed_root / gate.prior_build.PROFILE_PATH).read_bytes())
    proof = json.loads((reviewed_root / gate.SOURCE_REVIEW).read_bytes())
    scope = historical['expected']['source_scan']
    observed = {'scan_targets': deepcopy(scope['scan_targets']),
                'target_inventory_sha256': deepcopy(scope['target_inventory_sha256']),
                'findings': deepcopy(proof['unchanged']['finding_identities'])}
    observed['target_inventory_sha256'][TARGET] = proof['package_migration']['current_inventory_sha256']
    scan = {'Results': [{'Target': TARGET, 'Class': 'lang-pkgs', 'Type': 'pom',
                        'Packages': [{'Name': name, 'Version': version}
                                     for name, version in proof['package_migration']['current_complete_inventory']]}]}
    return scan, observed, historical, reviewed_root


def test_review_selects_only_one_complete_inventory_and_does_not_mutate_history(review_inputs):
    scan, observed, historical, root = review_inputs
    before = deepcopy(historical)
    reviewed = gate.reviewed_source_profile(scan, observed, historical, root)
    assert historical == before
    expected = deepcopy(before)
    expected['expected']['source_scan']['target_inventory_sha256'][TARGET] = (
        '49fbcecf0d3652dbf92ee2cf8bd4d6b7ade39503eeb43e767013216afcf6cee3')
    assert reviewed == expected
    assert reviewed['expected']['source_scan']['reviewed_findings'] == before['expected']['source_scan']['reviewed_findings']


@pytest.mark.parametrize('mutation', ['rollback', 'future-version', 'other-version', 'missing', 'extra', 'duplicate'])
def test_complete_110_package_scope_cannot_hide_any_other_change(review_inputs, mutation):
    scan, observed, historical, root = review_inputs
    packages = scan['Results'][0]['Packages']
    tomcat = next(row for row in packages if row['Name'].endswith(':tomcat-embed-core'))
    if mutation == 'rollback':
        tomcat['Version'] = '11.0.25'
    elif mutation == 'future-version':
        tomcat['Version'] = '11.0.28'
    elif mutation == 'other-version':
        next(row for row in packages if not row['Name'].startswith('org.apache.tomcat.embed:'))['Version'] = 'unreviewed'
    elif mutation == 'missing':
        packages.pop()
    elif mutation == 'extra':
        packages.append({'Name': 'example:unreviewed', 'Version': '1'})
    else:
        packages.append(deepcopy(tomcat))
    with pytest.raises(ValueError):
        gate.reviewed_source_profile(scan, observed, historical, root)


@pytest.mark.parametrize('mutation', ['target-added', 'target-missing', 'target-type', 'other-inventory',
                                     'inventory-missing', 'old-pom-inventory', 'duplicate-pom', 'missing-pom'])
def test_every_original_target_and_inventory_remains_mandatory(review_inputs, mutation):
    scan, observed, historical, root = review_inputs
    if mutation == 'target-added':
        observed['scan_targets'].append(['new/go.mod', 'lang-pkgs', 'gomod'])
    elif mutation == 'target-missing':
        observed['scan_targets'].pop()
    elif mutation == 'target-type':
        observed['scan_targets'][0][2] = 'other'
    elif mutation == 'other-inventory':
        name = next(name for name in observed['target_inventory_sha256'] if name != TARGET)
        observed['target_inventory_sha256'][name] = '0' * 64
    elif mutation == 'inventory-missing':
        observed['target_inventory_sha256'].pop(next(name for name in observed['target_inventory_sha256'] if name != TARGET))
    elif mutation == 'old-pom-inventory':
        observed['target_inventory_sha256'][TARGET] = historical['expected']['source_scan']['target_inventory_sha256'][TARGET]
    elif mutation == 'duplicate-pom':
        scan['Results'].append(deepcopy(scan['Results'][0]))
    else:
        scan['Results'].clear()
    with pytest.raises(ValueError):
        gate.reviewed_source_profile(scan, observed, historical, root)


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'duplicate', 'order', 'id', 'target', 'package', 'version', 'severity'])
def test_all_71_original_finding_tuples_remain_exact(review_inputs, mutation):
    scan, observed, historical, root = review_inputs
    rows = observed['findings']
    if mutation == 'missing':
        rows.pop()
    elif mutation == 'extra':
        rows.append({**rows[0], 'id': 'CVE-unreviewed'})
    elif mutation == 'duplicate':
        rows[-1] = deepcopy(rows[0])
    elif mutation == 'order':
        rows.reverse()
    else:
        rows[0][mutation] = 'LOW' if mutation == 'severity' else 'unreviewed'
    with pytest.raises(ValueError):
        gate.reviewed_source_profile(scan, observed, historical, root)


@pytest.mark.parametrize('field', ['findings', 'compiled-proof'])
def test_original_review_facts_cannot_be_silently_renewed(review_inputs, field):
    scan, observed, historical, root = review_inputs
    if field == 'findings':
        historical['expected']['source_scan']['reviewed_findings'].pop()
    else:
        historical['expected']['source_scan']['reviewed_findings'][0]['proof']['kind'] = 'unreviewed'
    with pytest.raises(ValueError, match='Original complete source scope'):
        gate.reviewed_source_profile(scan, observed, historical, root)


@pytest.mark.parametrize('name', [TARGET, gate.SOURCE_REVIEW, gate.prior_build.PROFILE_PATH,
                                'docs/evidence/release-v014-20261112/backend-tomcat27-acceptance.json'])
def test_source_and_all_bound_review_evidence_are_checked(review_inputs, tmp_path, name):
    scan, observed, historical, root = review_inputs
    proof = json.loads((root / gate.SOURCE_REVIEW).read_bytes())
    for path in {TARGET, gate.SOURCE_REVIEW, gate.prior_build.PROFILE_PATH, *proof['evidence_sha256']}:
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / path, destination)
    with (tmp_path / name).open('ab') as stream:
        stream.write(b'\n')
    with pytest.raises(ValueError):
        gate.reviewed_source_profile(scan, observed, historical, tmp_path)
