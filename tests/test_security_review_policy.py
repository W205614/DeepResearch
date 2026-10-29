"""Calendar passage cannot widen the exact human-approved review scope."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from scripts import security_review_policy as gate


def write_policy(root, policy, monkeypatch):
    raw = json.dumps(policy, ensure_ascii=False, sort_keys=True).encode()
    path = root / gate.POLICY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    monkeypatch.setattr(gate, 'POLICY_SHA256', hashlib.sha256(raw).hexdigest())
    return path


@pytest.fixture
def amendment(tmp_path, monkeypatch):
    records = {}
    for name in gate.RECORD_PATHS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps({'record': name, 'expires': '2026-11-25T23:59:59+08:00'}).encode()
        path.write_bytes(raw)
        records[name] = hashlib.sha256(raw).hexdigest()
    policy = {
        'schema': 'change-driven-security-review-policy-v1',
        'accepted': True,
        'actor_kind': 'human_user_via_chat',
        'human_instruction': gate.HUMAN_INSTRUCTION,
        'validity': gate.VALIDITY_POLICY,
        'conditions': dict.fromkeys(gate.CONDITIONS, True),
        'records_sha256': records,
    }
    path = write_policy(tmp_path, policy, monkeypatch)
    return tmp_path, policy, path


@pytest.mark.parametrize('year', [2027, 2099])
def test_unchanged_exact_records_do_not_expire_by_calendar(amendment, year):
    root, policy, _ = amendment
    for name in gate.RECORD_PATHS:
        assert gate.validate_review_policy(root, Path(name), now=datetime(year, 1, 1, tzinfo=timezone.utc)) == policy


@pytest.mark.parametrize('target', ['policy', 'record'])
@pytest.mark.parametrize('change', ['missing', 'tampered'])
def test_missing_or_changed_policy_or_record_fails(amendment, target, change):
    root, _, policy_path = amendment
    name = sorted(gate.RECORD_PATHS)[0]
    path = policy_path if target == 'policy' else root / name
    if change == 'missing':
        path.rename(path.with_suffix('.missing'))
    else:
        path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError):
        gate.validate_review_policy(root, name)


@pytest.mark.parametrize('change', ['schema', 'accepted', 'actor', 'instruction', 'validity',
                                   'condition', 'extra_condition', 'missing_record', 'extra_record', 'digest'])
def test_even_rehashed_policy_cannot_broaden_or_weaken_review(amendment, monkeypatch, change):
    root, original, _ = amendment
    policy = deepcopy(original)
    if change == 'schema':
        policy['schema'] = 'unreviewed'
    elif change == 'accepted':
        policy['accepted'] = 1
    elif change == 'actor':
        policy['actor_kind'] = 'automated_agent'
    elif change == 'instruction':
        policy['human_instruction'] = 'Ignore every vulnerability'
    elif change == 'validity':
        policy['validity'] = 'unconditional'
    elif change == 'condition':
        policy['conditions']['fresh_complete_scans'] = False
    elif change == 'extra_condition':
        policy['conditions']['skip_all_checks'] = True
    elif change == 'missing_record':
        policy['records_sha256'].pop(sorted(gate.RECORD_PATHS)[-1])
    elif change == 'extra_record':
        policy['records_sha256']['deployment/security/unreviewed.json'] = '0' * 64
    else:
        policy['records_sha256'][sorted(gate.RECORD_PATHS)[-1]] = 'not-a-digest'
    write_policy(root, policy, monkeypatch)
    with pytest.raises(ValueError):
        gate.validate_review_policy(root, sorted(gate.RECORD_PATHS)[0])


@pytest.mark.parametrize('raw', [b'[]', b'{', b'{"accepted":false,"accepted":true}', b'{"accepted":NaN}'])
def test_malformed_or_ambiguous_policy_is_not_approval(amendment, monkeypatch, raw):
    root, _, path = amendment
    path.write_bytes(raw)
    monkeypatch.setattr(gate, 'POLICY_SHA256', hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError):
        gate.validate_review_policy(root, sorted(gate.RECORD_PATHS)[0])


@pytest.mark.parametrize('name', ['../deployment/security/minio-fixed-disposition.json',
                                 '/deployment/security/minio-fixed-disposition.json',
                                 'deployment\\security\\minio-fixed-disposition.json',
                                 'C:/deployment/security/minio-fixed-disposition.json',
                                 'deployment/security/unreviewed.json'])
def test_unlisted_or_escaping_record_cannot_inherit_amendment(amendment, name):
    with pytest.raises(ValueError):
        gate.validate_review_policy(amendment[0], name)


def test_naive_clock_is_rejected(amendment):
    with pytest.raises(ValueError, match='timezone'):
        gate.validate_review_policy(amendment[0], sorted(gate.RECORD_PATHS)[0], now=datetime(2099, 1, 1))


@pytest.mark.parametrize('target', ['policy', 'record'])
def test_linked_policy_or_record_cannot_substitute_bytes(amendment, target):
    root, _, policy_path = amendment
    name = sorted(gate.RECORD_PATHS)[0]
    path = policy_path if target == 'policy' else root / name
    actual = path.with_suffix('.actual')
    path.rename(actual)
    try:
        path.symlink_to(actual)
    except OSError as error:
        pytest.skip('Host does not permit test symlinks: ' + type(error).__name__)
    with pytest.raises(ValueError, match='Linked'):
        gate.validate_review_policy(root, name)
