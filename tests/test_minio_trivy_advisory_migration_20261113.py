"""Only the exact three reviewed BIG5 tuples may leave a complete raw inventory."""
import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts import check_minio_os_20261113 as gate

ROOT = Path(__file__).resolve().parents[1]
PROOF = 'deployment/security/minio-os-20261113-evidence/trivy-advisory-20261113/migration.json'


@pytest.fixture
def migration(tmp_path):
    proof = json.loads((ROOT / PROOF).read_bytes())
    profile = json.loads((ROOT / gate.PROFILE_PATH).read_bytes())
    files = {PROOF, proof['before']['path'], proof['after']['path'], proof['official_database']['manifest']['path']}
    for name in files:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
        profile['evidence_sha256'][name] = gate.digest(target)
    profile['trivy_advisory_migration'] = {'path': PROOF, 'sha256': gate.digest(tmp_path / PROOF)}
    after = json.loads(gzip.decompress((tmp_path / proof['after']['path']).read_bytes()))
    profile['reviewed_findings']['Trivy'] = gate.trivy_rows(after)[0]
    return profile, proof, after, tmp_path


def test_exact_complete_actual_database_migration(migration):
    profile, proof, _, root = migration
    rows = gate.verify_trivy_advisory_migration(profile, root)
    assert len(rows) == 3
    assert {row['package'] for row in rows} == {'glibc', 'glibc-common', 'glibc-minimal-langpack'}
    assert all(row['id'] == 'CVE-2026-122054' and row['severity'] == 'MEDIUM' for row in rows)


@pytest.mark.parametrize('change', ['other_missing', 'same_count_substitution', 'changed_severity',
                                   'duplicate_removed', 'wrong_database', 'wrong_database_time'])
def test_same_count_or_rebound_metadata_cannot_authorize_other_drift(migration, change):
    profile, proof, after, root = migration
    result = next(row for row in after['Results'] if row.get('Vulnerabilities'))
    if change == 'other_missing':
        result['Vulnerabilities'].pop()
    elif change == 'same_count_substitution':
        result['Vulnerabilities'][0]['VulnerabilityID'] = 'CVE-UNREVIEWED'
    elif change == 'changed_severity':
        row = result['Vulnerabilities'][0]
        row['Severity'] = 'HIGH' if row['Severity'] != 'HIGH' else 'LOW'
    elif change == 'duplicate_removed':
        proof['removed_exact_rows'][0] = copy.deepcopy(proof['removed_exact_rows'][1])
    elif change == 'wrong_database':
        path = proof['official_database']['manifest']['path']
        (root / path).write_bytes(b'{"layers":[]}\n')
        value = gate.digest(root / path)
        profile['evidence_sha256'][path] = proof['official_database']['manifest']['sha256'] = value
    else:
        proof['official_database']['metadata']['UpdatedAt'] = '2026-11-12T07:00:07Z'
    # Rebind transport hashes too: this must fail the exact semantic contract,
    # rather than succeed by editing a JSON digest along with the payload.
    target = root / proof['after']['path']
    target.write_bytes(gzip.compress(json.dumps(after).encode(), mtime=0))
    profile['evidence_sha256'][proof['after']['path']] = proof['after']['sha256'] = gate.digest(target)
    profile['reviewed_findings']['Trivy'] = gate.trivy_rows(after)[0]
    raw = (json.dumps(proof, indent=2) + '\n').encode()
    (root / PROOF).write_bytes(raw)
    value = hashlib.sha256(raw).hexdigest()
    profile['evidence_sha256'][PROOF] = profile['trivy_advisory_migration']['sha256'] = value
    with pytest.raises(ValueError):
        gate.verify_trivy_advisory_migration(profile, root)


def test_without_exact_migration_original_missing_gate_still_blocks():
    profile = json.loads((ROOT / gate.PROFILE_PATH).read_bytes())
    profile.pop('trivy_advisory_migration', None)
    proof = json.loads((ROOT / PROOF).read_bytes())
    profile['reviewed_findings']['Trivy'] = gate.trivy_rows(
        json.loads(gzip.decompress((ROOT / proof['after']['path']).read_bytes())))[0]
    matrix = json.loads((ROOT / profile['os_resolution_evidence']).read_bytes())
    with pytest.raises(ValueError, match='row disappeared or changed'):
        gate.verify_os_matrix(profile, matrix, ROOT)
