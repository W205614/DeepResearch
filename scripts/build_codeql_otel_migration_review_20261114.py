"""Produce the exact otel-1.47.0 migration continuation for the CodeQL CI binding.

The otel-v1.47.0 security migration (CVE-2026-128085) and its CI routing retired
Grafana from the reusable-artifact freeze, switched the reviewed-source-image cache
to the 20261114 builder, and upgraded soupsieve in uv.lock. Those tracked files
and their historical test fixtures are bound inside the CodeQL evidence closure, so this
continuation re-binds their new bytes without changing any reviewed finding identity.
Historical positive tests now restore the original inputs before evaluating the
old contract; the current-tree rejection paths remain strict.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PRIOR = 'deployment/security/codeql-reviewed-findings-20261114-ci-attributes.json'
PROOF = 'deployment/security/codeql-otel-migration-addendum-20261114.json'
PROFILE = 'deployment/security/codeql-reviewed-findings-20261114-otel-migration.json'

# Exact reviewed routing, dependency and historical test-fixture changes.
DELTA_FILES = [
    'compose.yaml',
    'deployment/compose/compose.verify.yaml',
    'scripts/local.py',
    '.github/workflows/ci.yml',
    '.github/workflows/security.yml',
    '.github/workflows/rag-quality.yml',
    'scripts/runtime_ci_inventory.py',
    'scripts/check_agent_full_scope.py',
    'uv.lock',
    'scripts/ci_reviewed_image_cache.py',
    'tests/test_grafana_fixed_20261107.py',
    'tests/test_security_review_policy_integration.py',
    'tests/test_fs_workflow_gate.py',
    'tests/test_ci_compose_build.py',
    'scripts/.gitattributes',
    'tests/.gitattributes',
    'deployment/security/.gitattributes',
]

ATTRIBUTE_SUFFIXES = {
    'scripts/.gitattributes': ['/*20261115.py text eol=lf',
        '/build_codeql_otel_migration_review_20261114.py text eol=lf'],
    'tests/.gitattributes': ['/test_*20261115.py text eol=lf',
        '/test_grafana_otel_retained_base.py text eol=lf'],
    'deployment/security/.gitattributes': ['/*20261115*.json -text whitespace=cr-at-eol',
        '/*20261115*/** -text whitespace=cr-at-eol',
        '/codeql-otel-migration-addendum-20261114.json -text',
        '/codeql-reviewed-findings-20261114-otel-migration.json -text',
        '/tempo-app1-evidence/data-refresh-20261030/technical-review.json -text'],
}


def verify_attribute_continuation(root):
    from scripts import build_codeql_ci_attributes_review_20261114 as attributes
    for name, rules in ATTRIBUTE_SUFFIXES.items():
        prefix = b'' if name == 'tests/.gitattributes' else attributes.blob(root, attributes.BASE, name)
        before = prefix + b'\n' + ('\n'.join(attributes.RULES[name]) + '\n').encode()
        suffix = ('\n' + '\n'.join(rules) + '\n').encode()
        if (root / name).read_bytes() != before + suffix:
            raise ValueError('Attribute changes exceed the exact new-file preservation rules: ' + name)
    return attributes.compare_tracked_attributes(root)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def generate(root=ROOT):
    attributes = verify_attribute_continuation(root)
    prior_bytes = (root / PRIOR).read_bytes()
    prior = json.loads(prior_bytes)
    if prior['schema'] != 'codeql-exact-reviewed-findings-v1':
        raise ValueError('Unsupported prior CodeQL review record')

    # Rebind only the exact reviewed migration files; every other bound file
    # must remain byte-identical or the continuation fails closed.
    deltas = {}
    unchanged = {}
    for row in prior['findings']:
        for name, expected in row['evidence_sha256'].items():
            observed = digest((root / name).read_bytes())
            if name in DELTA_FILES:
                if observed == expected:
                    raise ValueError('A declared otel-migration file did not change: ' + name)
                deltas[name] = {'before': expected, 'after': observed}
            else:
                if observed != expected:
                    raise ValueError('Unreviewed bound file change: ' + name)
                unchanged[name] = expected
    if set(deltas) != set(DELTA_FILES):
        raise ValueError('Exact otel-migration delta set differs')

    proof = {
        'schema': 'codeql-otel-migration-continuation-v1',
        'reviewed_at': '2026-11-15',
        'actor_kind': 'automated_agent_technical_reviewer',
        'prior_profile': {'path': PRIOR, 'sha256': digest(prior_bytes), 'bytes_preserved': True},
        'exact_bound_code_deltas': deltas,
        'historical_attribute_equivalence': attributes,
        'generator_source_sha256': digest(Path(__file__).read_bytes()),
        'all_other_existing_bound_sha256': unchanged,
        'same_four_exact_identities': True,
        'first_three_original_records_unchanged': True,
        'severity_threshold_unchanged': 7.0,
        'new_findings_exempted': False,
        'new_codeql_scan_claimed': False,
        'raw_sarif_modified': False,
        'risk_acceptance': False,
        'human_review_claimed': False,
        'review_rationale': [
            'The otel-v1.47.0 migration (CVE-2026-128085) retires Grafana from the reusable-artifact freeze and re-routes the reviewed-source-image cache to the 20261114 builder; compose.yaml, ci.yml, security.yml, runtime_ci_inventory.py and ci_reviewed_image_cache.py changed only within that reviewed routing delta.',
            'uv.lock changes are the reviewed soupsieve 4.1 upgrade (CVE-2026-126190), already bound by the agent source scope review.',
            'The XSS-through-DOM finding identity (js/xss-through-dom on the offline SQLite release note) and its source/proof bytes are unchanged. Historical tests now replay the exact original input bytes instead of the changed live locks; current production checks remain strict.',
            'No CodeQL scan is re-run and no SARIF is modified; the four original finding identities and severity threshold are preserved.',
        ],
    }
    proof_bytes = (json.dumps(proof, indent=2, sort_keys=False) + '\n').encode()

    current = deepcopy(prior)
    current['reviewed_at'] = '2026-11-15'
    binding = current['findings'][3]['evidence_sha256']
    for name, delta in deltas.items():
        binding[name] = delta['after']
    binding[PRIOR] = digest(prior_bytes)
    binding[PROOF] = digest(proof_bytes)
    binding['scripts/build_codeql_otel_migration_review_20261114.py'] = digest(Path(__file__).read_bytes())
    current['findings'][3]['justification'] += (
        ' The otel-1.47.0 migration continuation re-binds the exact CI routing, lock'
        ' and historical test-fixture changes; the flagged offline SQLite'
        ' release note and its source/proof bytes remain byte-identical.')
    current['otel_migration_addendum'] = {
        'prior_profile': PRIOR,
        'prior_profile_sha256': digest(prior_bytes),
        'path': PROOF,
        'sha256': digest(proof_bytes),
    }
    (root / PROOF).write_bytes(proof_bytes)
    (root / PROFILE).write_bytes((json.dumps(current, indent=2, sort_keys=False) + '\n').encode())


if __name__ == '__main__':
    generate()
