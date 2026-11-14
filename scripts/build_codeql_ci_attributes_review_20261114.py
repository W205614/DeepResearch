"""Preserve historical root attributes while keeping exact CI support bytes portable."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_codeql_ci_disk_review_20261114 as original  # noqa: E402

BASE = '4a860d2dc322847b1eec793c1ad5153cb90e2c65'
PRIOR = original.PROFILE
PROOF = 'deployment/security/codeql-ci-attributes-addendum-20261114.json'
PROFILE = 'deployment/security/codeql-reviewed-findings-20261114-ci-attributes.json'
RULES = {
    'scripts/.gitattributes': [
        '/ci_compose_build.py text eol=lf',
        '/build_codeql_ci_disk_review_20261114.py text eol=lf',
        '/build_codeql_ci_attributes_review_20261114.py text eol=lf'],
    'tests/.gitattributes': [
        '/test_ci_compose_build.py text eol=lf', '/test_fs_workflow_gate.py text eol=lf'],
    'deployment/security/.gitattributes': [
        '/codeql-ci-disk-addendum-20261114.json -text',
        '/codeql-reviewed-findings-20261114-ci-disk.json -text',
        '/codeql-ci-attributes-addendum-20261114.json -text',
        '/codeql-reviewed-findings-20261114-ci-attributes.json -text'],
}


def blob(root, revision, name):
    return subprocess.run(['git', 'cat-file', 'blob', revision + ':' + name],
                          cwd=root, check=True, capture_output=True).stdout


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_attributes(root):
    if (root / '.gitattributes').read_bytes() != blob(ROOT, original.BASE, '.gitattributes'):
        raise ValueError('Historical root attributes must remain byte-identical')
    for name, lines in RULES.items():
        prefix = b'' if name == 'tests/.gitattributes' else blob(ROOT, BASE, name)
        expected = prefix + b'\n' + ('\n'.join(lines) + '\n').encode()
        if (root / name).read_bytes() != expected:
            raise ValueError('Exact nested attribute rules are missing or changed: ' + name)


def compare_tracked_attributes(root):
    paths = subprocess.run(['git', 'ls-tree', '-rz', '--name-only', BASE], cwd=root,
                           check=True, capture_output=True).stdout
    with tempfile.TemporaryDirectory(prefix='ci-attribute-baseline-') as directory:
        baseline = Path(directory)
        subprocess.run(['git', 'init', '-q', str(baseline)], check=True)
        for raw in paths.split(b'\0'):
            if raw and Path(raw.decode()).name == '.gitattributes':
                name = raw.decode()
                path = baseline / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob(root, BASE, name))
        def attributes(cwd):
            return subprocess.run(['git', 'check-attr', '-z', '--stdin', '--all'],
                                  cwd=cwd, input=paths, check=True, capture_output=True).stdout
        before, after = attributes(baseline), attributes(root)
        if before != after:
            raise ValueError('Effective attributes changed for previously tracked files')
    return {'tracked_path_count': len(paths.split(b'\0')) - 1,
            'all_effective_attributes_identical': True, 'attribute_output_sha256': digest(after)}


def generate(root=ROOT):
    verify_attributes(root)
    equivalence = compare_tracked_attributes(root)
    prior_bytes = (root / PRIOR).read_bytes()
    assert prior_bytes == blob(root, BASE, PRIOR)
    prior = json.loads(prior_bytes)
    deltas = {}
    for record in prior['findings']:
        for name, expected in record['evidence_sha256'].items():
            observed = digest((root / name).read_bytes())
            if observed != expected:
                if name not in {'.gitattributes', 'tests/test_ci_compose_build.py'}:
                    raise ValueError('Unreviewed bound-file change: ' + name)
                deltas[name] = {'before': expected, 'after': observed}
    assert set(deltas) == {'.gitattributes', 'tests/test_ci_compose_build.py'}
    supports = {name: digest((root / name).read_bytes()) for name in [
        *RULES, 'scripts/build_codeql_ci_attributes_review_20261114.py']}
    proof = {
        'schema': 'codeql-ci-nested-attributes-continuation-v1',
        'reviewed_at': '2026-11-14', 'actor_kind': 'automated_agent_technical_reviewer',
        'prior_profile': {'path': PRIOR, 'sha256': digest(prior_bytes), 'bytes_preserved': True},
        'exact_bound_code_deltas': deltas, 'new_support_sha256': supports,
        'attribute_equivalence': equivalence,
        'historical_root_restored_from': original.BASE,
        'historical_root_sha256': digest((root / '.gitattributes').read_bytes()),
        'existing_nested_file_bytes_preserved_as_prefixes': True,
        'review_rationale': [
            'The old Grafana c6 fixture correctly requires its original root .gitattributes bytes; restoring them fixes the two setup errors without changing that fixture or its verifier.',
            'The same six CI support preservation rules move into anchored nested paths; three additional rules protect only the successor generator/proof/profile.',
            'All previously tracked paths have identical effective git attributes; current recipe, runtime COPY, source checks and CI scheduling are unchanged.',
            'The CI portability test now copies the full nested attribute hierarchy into a temporary autocrlf=true repository and still rejects missing or tampered rules.',
            'All previous findings, evidence, source labels and raw failures remain; this does not address the independently discovered new vulnerability inventory.',
        ],
        'same_four_exact_identities': True, 'first_three_original_records_unchanged': True,
        'severity_threshold_unchanged': 7.0, 'new_findings_exempted': False,
        'new_codeql_scan_claimed': False, 'raw_sarif_modified': False,
        'human_review_claimed': False, 'risk_acceptance': False,
    }
    raw = (json.dumps(proof, indent=2) + '\n').encode()
    current = deepcopy(prior)
    binding = current['findings'][3]['evidence_sha256']
    binding.update({name: value['after'] for name, value in deltas.items()})
    binding.update(supports)
    binding[PRIOR] = digest(prior_bytes)
    binding[PROOF] = digest(raw)
    current['findings'][3]['justification'] += (
        ' The exact nested-attributes continuation restores the historical root metadata and proves'
        ' identical effective attributes for all prior tracked paths, without runtime or selector changes.')
    current['ci_attributes_addendum'] = {'prior_profile': PRIOR, 'prior_profile_sha256': digest(prior_bytes),
                                        'path': PROOF, 'sha256': digest(raw)}
    (root / PROOF).write_bytes(raw)
    (root / PROFILE).write_bytes((json.dumps(current, indent=2) + '\n').encode())


if __name__ == '__main__':
    generate()
