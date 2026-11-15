"""Current transport source binding and both complete installed source contracts."""
from copy import deepcopy
import json
import shutil

import pytest

from scripts import check_backend_busybox_20261113 as gate


@pytest.fixture
def current_root(tmp_path):
    profile = json.loads((gate.ROOT / gate.TRANSPORT_PROFILE).read_bytes())
    names = set(profile['source_inputs']) | set(profile['evidence_sha256']) | {gate.TRANSPORT_PROFILE.as_posix()}
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(gate.ROOT / name, target)
    return tmp_path


def write_profile(root, value):
    (root / gate.TRANSPORT_PROFILE).write_bytes((json.dumps(value, indent=2) + '\n').encode())


def test_current_profile_preserves_both_complete_real_source_contracts(current_root):
    profile = gate.load_profile(current_root)
    prior = json.loads((current_root / gate.PROFILE).read_bytes())
    assert profile['schema'] == gate.TRANSPORT_SCHEMA
    assert profile['payload'] == prior['payload']
    assert profile['retained_finding_identities'] == prior['retained_finding_identities']
    assert len(profile['legal_source_variants']['retained']) == len(profile['legal_source_variants']['rebuilt']) == 83
    for name, closure in profile['legal_source_variants'].items():
        assert gate.legal_source_variant(closure, profile) == name
    changed = {name for name in prior['legal_source_files']
               if profile['legal_source_variants']['rebuilt'][name] != prior['legal_source_files'][name]}
    assert changed == {gate.LEGAL_RECIPE}


@pytest.mark.parametrize('change', ['extra', 'missing', 'recipe', 'other-source'])
def test_no_other_installed_legal_or_source_closure_can_use_transport_review(current_root, change):
    profile = gate.load_profile(current_root)
    closure = deepcopy(profile['legal_source_variants']['rebuilt'])
    if change == 'extra':
        closure['unreviewed-file'] = {'bytes': 1, 'sha256': '0' * 64}
    elif change == 'missing':
        del closure[gate.LEGAL_RECIPE]
    else:
        name = gate.LEGAL_RECIPE if change == 'recipe' else 'metadata/build.json'
        closure[name]['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='complete legal/source closure differs'):
        gate.legal_source_variant(closure, profile)


def test_every_other_installed_source_or_legal_file_remains_exact(current_root):
    profile = gate.load_profile(current_root)
    rebuilt = profile['legal_source_variants']['rebuilt']
    others = set(rebuilt) - {gate.LEGAL_RECIPE}
    assert len(others) == 82
    for name in others:
        closure = deepcopy(rebuilt)
        closure[name]['sha256'] = '0' * 64
        with pytest.raises(ValueError, match='complete legal/source closure differs'):
            gate.legal_source_variant(closure, profile)


@pytest.mark.parametrize('change', ['payload', 'raw-severity', 'source-set', 'source-hash', 'official-url',
                                  'original-body', 'legal-contract', 'retained-evidence', 'new-compile-claim'])
def test_transport_profile_cannot_change_fixed_source_payload_or_raw_contract(current_root, change):
    profile = json.loads((current_root / gate.TRANSPORT_PROFILE).read_bytes())
    if change == 'payload':
        profile['payload']['/bin/busybox']['sha256'] = '0' * 64
    elif change == 'raw-severity':
        profile['retained_finding_identities'][0][4] = 'Low'
    elif change == 'source-set':
        profile['source_inputs']['unreviewed'] = '0' * 64
    elif change == 'source-hash':
        profile['source_inputs'][gate.PRODUCER] = '0' * 64
    elif change == 'official-url':
        profile['transport_continuation']['assets'][0]['url'] += '?unreviewed'
    elif change == 'original-body':
        profile['transport_continuation']['original_recipe_utf8'] += '\n'
    elif change == 'legal-contract':
        profile['legal_source_variants']['retained'][gate.LEGAL_RECIPE]['sha256'] = '0' * 64
    elif change == 'retained-evidence':
        profile['evidence_sha256'][gate.PROFILE.as_posix()] = '0' * 64
    else:
        profile['collection']['new_java_compilation_or_jre_scan_claimed'] = True
    write_profile(current_root, profile)
    with pytest.raises(ValueError):
        gate.load_profile(current_root)


@pytest.mark.parametrize('change', ['retry', 'extra-url'])
def test_any_recipe_change_beyond_exact_four_urls_is_rejected(current_root, change):
    recipe = current_root / gate.RECIPE
    before = recipe.read_bytes()
    after = (before.replace(b'--retry 2', b'--retry 3', 1) if change == 'retry' else
             before.replace(gate.transport_assets()[0]['url'].encode(),
                            (gate.transport_assets()[0]['url'] + '?unreviewed').encode(), 1))
    assert after != before
    recipe.write_bytes(after)
    with pytest.raises(ValueError, match='beyond the four exact transport URLs'):
        gate.load_profile(current_root)


def test_original_frozen_profile_is_not_rewritten_or_relabelled(current_root):
    original = current_root / gate.PROFILE
    original.write_bytes(original.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='Original Backend BusyBox profile changed'):
        gate.load_profile(current_root)


def test_producer_source_is_part_of_current_reviewed_input_set(current_root):
    producer = current_root / gate.PRODUCER
    producer.write_bytes(producer.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='source input set differs'):
        gate.load_profile(current_root)
