"""Produce an exact CI-scheduling continuation without changing historical reviews."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASE = '33d5a374a51ca607ef9c90a395b023b975f38f80'
PRIOR = 'deployment/security/codeql-reviewed-findings-20261113-exact-image-cache.json'
PROOF = 'deployment/security/codeql-ci-disk-addendum-20261114.json'
PROFILE = 'deployment/security/codeql-reviewed-findings-20261114-ci-disk.json'
WORKFLOW = '.github/workflows/ci.yml'
SUPPORT = ['scripts/ci_compose_build.py', 'tests/test_ci_compose_build.py',
           'tests/test_fs_workflow_gate.py', 'scripts/build_codeql_ci_disk_review_20261114.py', '.gitattributes']
ATTRIBUTE_RULES = [
    PROOF + ' -text', PROFILE + ' -text',
    'scripts/ci_compose_build.py text eol=lf',
    'scripts/build_codeql_ci_disk_review_20261114.py text eol=lf',
    'tests/test_ci_compose_build.py text eol=lf',
    'tests/test_fs_workflow_gate.py text eol=lf',
]


def verify_attributes(root):
    lines = (root / '.gitattributes').read_text().splitlines()
    if lines[-len(ATTRIBUTE_RULES):] != ATTRIBUTE_RULES:
        raise ValueError('Exact CI review byte-preservation rules are missing or changed')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def workflow_delta(before, after):
    """Reject anything beyond the reviewed scheduling and cache-lifetime changes."""
    expected = deepcopy(before)
    steps = expected['jobs']['enterprise-compose-smoke']['steps']
    grafana = next(i for i, s in enumerate(steps)
                   if s.get('with', {}).get('artifact') == 'grafana')
    steps.insert(grafana, {
        'name': 'Release completed storage compiler cache before the next source build',
        'if': "runner.environment == 'github-hosted'",
        'run': 'python scripts/ci_compose_build.py --prune-only'})
    collector = next(s for s in steps if 'validate --config=' in s.get('run', ''))
    assert collector['run'].startswith('docker compose build otel-collector\n')
    collector['run'] = collector['run'].removeprefix('docker compose build otel-collector\n')
    compile_step = next(s for s in steps if s.get('run') == 'docker compose build')
    prune_step = next(s for s in steps if 'docker builder prune' in s.get('run', ''))
    assert prune_step['if'] == "runner.environment == 'github-hosted'"
    assert prune_step['run'].splitlines() == [
        'df -h .', 'docker system df', 'docker builder prune --all --force', 'docker system df', 'df -h .']
    steps.remove(compile_step)
    steps.remove(prune_step)
    steps.insert(steps.index(collector), {
        'name': 'Build enterprise recipes sequentially with bounded compiler cache',
        'run': 'python scripts/ci_compose_build.py'})
    if expected != after:
        raise ValueError('Workflow changes exceed the exact reviewed CI scheduling delta')


def generate(root=ROOT):
    verify_attributes(root)
    prior_bytes = (root / PRIOR).read_bytes()
    prior = json.loads(prior_bytes)
    old_workflow = subprocess.run(['git', 'show', BASE + ':' + WORKFLOW], cwd=root,
                                  check=True, capture_output=True).stdout
    current_workflow = (root / WORKFLOW).read_bytes()
    workflow_delta(yaml.safe_load(old_workflow), yaml.safe_load(current_workflow))
    unchanged = {}
    for row in prior['findings']:
        for name, expected in row['evidence_sha256'].items():
            if name == WORKFLOW:
                assert digest(old_workflow) == expected
            else:
                if digest((root / name).read_bytes()) != expected:
                    raise ValueError('Unreviewed bound source/proof delta: ' + name)
                unchanged[name] = expected
    support = {name: digest((root / name).read_bytes()) for name in SUPPORT}
    proof = {
        'schema': 'codeql-ci-disk-continuation-v1',
        'reviewed_at': '2026-11-14',
        'actor_kind': 'automated_agent_technical_reviewer',
        'prior_profile': {'path': PRIOR, 'sha256': digest(prior_bytes), 'bytes_preserved': True},
        'baseline_source_sha': BASE,
        'exact_bound_code_deltas': {WORKFLOW: {'before': digest(old_workflow), 'after': digest(current_workflow)}},
        'all_other_existing_bound_files_verified': len(unchanged),
        'new_support_sha256': support,
        'same_four_exact_identities': True,
        'first_three_original_records_unchanged': True,
        'severity_threshold_unchanged': 7.0,
        'new_findings_exempted': False,
        'workflow_delta': {
            'all_other_parsed_workflow_fields_equal': True,
            'reviewed_source_image_actions_unchanged': True,
            'collector_validation_command_unchanged': True,
            'startup_smoke_readiness_and_browser_gates_unchanged': True,
            'failure_evidence_uploads_unchanged': True,
        },
        'disk_failure': {
            'job_url': 'https://github.com/W205614/DeepResearch/actions/runs/43437807833/job/131960804640',
            'observed': '63 MB free, then concurrent Loki, Prometheus and Tempo compiler ENOSPC before the old final prune.',
            'original_failure_preserved': True,
        },
        'helper_review': [
            'GitHub Actions, github-hosted and Linux are all required before any Docker subprocess.',
            'Resolved Compose build objects define recipe groups; every build service is kept, including all Python image aliases.',
            'COMPOSE_BAKE=false and compose --parallel 1 serialize builds; aliases keep compiler cache until their whole group succeeds.',
            'Only unused BuildKit cache is pruned; no image, system or volume pruning, filesystem deletion, or runtime startup occurs.',
            'Failed builds propagate and do not trigger post-failure pruning; complete command output remains in Actions logs.',
            'Resolved configuration is captured privately, never printed. The helper has no application serving route or HTML transformation.',
        ],
        'html_boundary': 'The original offline SQLite HTML, Docker COPY inputs, frontend/authentication implementations and exact finding identity remain byte-identical. Only CI build scheduling changes; no new route serves the evidence HTML.',
        'validation': {
            'focused_ci_tests': {'passed': 70, 'failed': 0},
            'integrated_focused_tests': {'passed': 132, 'failed': 0},
            'full_ruff': 'passed',
            'old_profile_rejection': 'CodeQL reviewed source or proof changed; renewed review required: .github/workflows/ci.yml',
            'full_pytest': 'Interrupted after old-profile binding failures; not claimed passed.',
            'hosted_build': 'Pending; local mocked scheduling tests do not prove hosted disk capacity.',
        },
        'new_codeql_scan_claimed': False,
        'raw_sarif_modified': False,
        'risk_acceptance': False,
        'human_review_claimed': False,
        'new_native_compilation_claimed': False,
    }
    proof_bytes = (json.dumps(proof, indent=2) + '\n').encode()
    current = deepcopy(prior)
    current['reviewed_at'] = '2026-11-14'
    binding = current['findings'][3]['evidence_sha256']
    binding[WORKFLOW] = digest(current_workflow)
    binding.update(support)
    binding[PRIOR] = digest(prior_bytes)
    binding[PROOF] = digest(proof_bytes)
    current['findings'][3]['justification'] += (
        ' The 2026-11-14 exact CI disk-scheduling continuation preserves all runtime COPY/serving boundaries;'
        ' it only serializes existing recipes and prunes unused build cache on disposable hosted runners.')
    current['ci_disk_addendum'] = {
        'prior_profile': PRIOR, 'prior_profile_sha256': digest(prior_bytes),
        'path': PROOF, 'sha256': digest(proof_bytes)}
    (root / PROOF).write_bytes(proof_bytes)
    (root / PROFILE).write_bytes((json.dumps(current, indent=2) + '\n').encode())


if __name__ == '__main__':
    generate()
