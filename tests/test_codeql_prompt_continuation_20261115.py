import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts import build_codeql_otel_migration_review_20261114 as producer
from scripts.check_codeql_results import REVIEW, reviewed_findings


ROOT = Path(__file__).resolve().parents[1]


def test_prompt_continuation_retains_all_original_identities_and_first_three_records():
    prior = json.loads((ROOT / producer.PRIOR).read_bytes())
    current = json.loads(REVIEW.read_bytes())
    proof = json.loads((ROOT / producer.PROOF).read_bytes())
    assert current['findings'][:3] == prior['findings'][:3]
    assert [r['identity'] for r in current['findings']] == [r['identity'] for r in prior['findings']]
    assert proof['severity_threshold_unchanged'] == 7.0
    assert not proof['new_findings_exempted'] and not proof['new_codeql_scan_claimed']
    assert 'backend/research/graph.py' in proof['exact_bound_code_deltas']
    assert set(proof['prompt_continuation_support_sha256']) == set(producer.PROMPT_CONTINUATION_SUPPORT)
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h
               for p, h in proof['prompt_continuation_support_sha256'].items())
    assert reviewed_findings([], REVIEW, ROOT) == ([], [])


@pytest.mark.parametrize('path', [
    'backend/research/graph.py',
    'deployment/security/agent-gap-coverage-20261115-profile.json',
    'deployment/security/agent-gap-coverage-20261115-evidence/runtime-probe.json',
    'scripts/check_ci_evaluation_budget.py',
    'eval/ci-budget-source-basis-20261115-prompts.json',
    'tests/conftest.py',
])
def test_current_prompt_source_and_evidence_tampering_still_blocks(tmp_path, path):
    review = json.loads(REVIEW.read_bytes())
    for record in review['findings']:
        for name in record['evidence_sha256']:
            destination = tmp_path / name
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, destination)
    current = tmp_path / 'current-review.json'
    current.write_bytes(REVIEW.read_bytes())
    assert reviewed_findings([], current, tmp_path) == ([], [])
    altered = tmp_path / path
    altered.write_bytes(altered.read_bytes() + b'\nunreviewed bytes\n')
    with pytest.raises(ValueError, match='reviewed source or proof changed'):
        reviewed_findings([], current, tmp_path)
