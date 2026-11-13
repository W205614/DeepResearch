"""Recheck current source tuples and explicitly migrate historical compiled facts to fixed Go builds."""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_scan_20261110 as prior  # noqa: E402
from scripts import check_grafana_source_build_20261110 as prior_build  # noqa: E402
from scripts import check_grafana_source_build_20261112 as current  # noqa: E402
from scripts.retained_source_review_20261112 import SOURCE, snapshot  # noqa: E402


SOURCE_REVIEW = 'deployment/security/grafana-source-20261112-evidence/source-fs-tomcat27-review-20261112.json'
SOURCE_REVIEW_SHA256 = '39aaec83119b9169548689c2f16f5fb4d2f21380fafc5e3df7ecf6f9d8589c09'
PYTHON_REVIEW = 'deployment/security/grafana-source-20261112-evidence/source-python-scope-20261113.json'
PYTHON_REVIEW_SHA256 = '34acad929e59d408d97e5fc9686aa497a53936984655ef548e55001649f936f3'


def python_source_scope(observed, root=ROOT):
    proof_path = prior_build.checked(root, PYTHON_REVIEW)
    current.require(current.digest(proof_path) == PYTHON_REVIEW_SHA256, 'Exact Python source-path proof changed')
    proof = prior.old.json_loads(proof_path.read_bytes())
    expected = [{'id': identifier, 'target': 'uv.lock', 'package': 'soupsieve', 'version': '2.9.2', 'severity': severity}
                for identifier, severity in (('CVE-2026-126190', 'HIGH'), ('CVE-2026-126192', 'MEDIUM'))]
    current.require(proof['schema'] == 'grafana-source-exact-two-python-caller-scope-v1'
                    and proof['upstream_package_affected'] is True and proof['package_fixed_claim'] is False
                    and proof['disposition'] == 'NOT_AFFECTED_FUNCTION_NOT_IN_CURRENT_APPLICATION_PATH'
                    and Counter(map(prior_build.finding_key, proof['new_exact_findings']))
                    == Counter(map(prior_build.finding_key, expected))
                    and len(observed['findings']) == proof['all_source_finding_count'] == 73
                    and observed['findings'] == proof['all_source_finding_identities']
                    and observed['target_inventory_sha256']['uv.lock'] == proof['uv_lock_inventory_sha256']
                    and current.digest(prior_build.checked(root, 'uv.lock')) == proof['uv_lock_sha256'],
                    'Complete73 findings/unchanged uv-lock inventory differ from the two exact Python findings')
    for name, expected_sha in proof['support_sha256'].items():
        current.require(current.digest(prior_build.checked(root, name)) == expected_sha,
                        'Current Python path/profile/support changed')
    files = proof['current_application_files_sha256']
    current.require({p.relative_to(root).as_posix() for p in (root / 'backend').rglob('*.py')
                     if '__pycache__' not in p.parts} == {name for name in files if name.endswith('.py')}
                    and all(current.digest(prior_build.checked(root, name)) == expected_sha
                            for name, expected_sha in files.items()), 'Current application caller source changed')
    return expected


def reviewed_source_profile(scan, observed, historical, root=ROOT):
    proof_path = prior_build.checked(root, SOURCE_REVIEW)
    current.require(current.digest(proof_path) == SOURCE_REVIEW_SHA256,
                    'Exact Tomcat source-inventory review changed')
    proof = prior.old.json_loads(proof_path.read_bytes())
    scope = historical['expected']['source_scan']
    current.require(proof['schema'] == 'source-fs-exact-tomcat27-migration-20261112-v1'
                    and current.digest(prior_build.checked(root, prior_build.PROFILE_PATH))
                    == proof['prior_profile']['sha256']
                    and hashlib.sha256(prior.old.canonical(scope)).hexdigest()
                    == proof['prior_profile']['source_scope_sha256'],
                    'Original complete source scope differs from the Tomcat review')
    pom = proof['pom']['path']
    current.require(current.digest(prior_build.checked(root, pom)) == proof['pom']['current_sha256'],
                    'Current POM differs from the exact Tomcat 27 repair')
    for name, expected in proof['evidence_sha256'].items():
        current.require(current.digest(prior_build.checked(root, name)) == expected,
                        'Tomcat source-inventory repair evidence changed')
    migration, unchanged = proof['package_migration'], proof['unchanged']
    targets = [row for row in scan['Results'] if row['Target'] == pom]
    current.require(len(targets) == 1, 'Exactly one complete POM target is required')
    packages = [list(row) for row in prior.old.inventory(targets[0]['Packages'])]
    current.require(packages == migration['current_complete_inventory']
                    and len(packages) == migration['package_count'] == 110,
                    'Complete 110-package Tomcat inventory differs from the review')
    previous = migration['prior_complete_inventory']
    changes = {row['package']: (row['before'], row['after']) for row in migration['exact_changes']}
    current.require(len(previous) == 110 and len(changes) == 3
                    and [[name, changes[name][1] if name in changes else version]
                         for name, version in previous] == packages
                    and all([name, versions[0]] in previous for name, versions in changes.items())
                    and hashlib.sha256(prior.old.canonical(previous)).hexdigest()
                    == migration['prior_inventory_sha256'] == scope['target_inventory_sha256'][pom]
                    and hashlib.sha256(prior.old.canonical(packages)).hexdigest()
                    == migration['current_inventory_sha256'],
                    'Only the three exact Tomcat versions may migrate')
    expected_inventory = {**unchanged['other_target_inventory_sha256'],
                          pom: migration['current_inventory_sha256']}
    current.require(observed['scan_targets'] == scope['scan_targets'] == unchanged['scan_targets']
                    and len(observed['scan_targets']) == unchanged['target_count'] == 79
                    and unchanged['other_target_inventory_sha256']
                    == {name: value for name, value in scope['target_inventory_sha256'].items() if name != pom}
                    and observed['target_inventory_sha256'] == expected_inventory,
                    'Other source targets or complete package inventories changed')
    findings = Counter(prior_build.finding_key(row) for row in observed['findings'])
    current.require(len(observed['findings']) == unchanged['finding_count'] == 71
                    and observed['findings'] == unchanged['finding_identities']
                    and findings == Counter(map(prior_build.finding_key, unchanged['finding_identities']))
                    == Counter(map(prior_build.finding_key, scope['reviewed_findings']))
                    and all(count == 1 for count in findings.values()),
                    'All 71 original source finding identities must remain unchanged')
    # Preserve the old profile and all its per-finding proofs. Only this separately
    # reviewed complete POM inventory is selected for the unchanged source gate.
    reviewed = deepcopy(historical)
    reviewed['expected']['source_scan']['target_inventory_sha256'] = expected_inventory
    return reviewed


def evaluate(scan, receipt, original, candidate, inventory, *, raw_sha256, original_sha256,
             source_commit, tracked_clean, root=ROOT):
    profile = current.load_profile(root)
    historical = prior_build.load_profile(snapshot(root))
    collection = prior.evaluate(scan, receipt, original, candidate, raw_sha256=raw_sha256,
        original_sha256=original_sha256, source_commit=source_commit, tracked_clean=tracked_clean,
        root=root, prepare=True, profile=historical, compiled_root=snapshot(root))
    # The scanner receipt and full inventories belong to the current commit.
    # The old compiled closure is explicitly historical: current's full native
    # and package-graph comparison above proves its non-stdlib facts still apply.
    python_rows = python_source_scope(collection['observed'], root)
    retained_observed = deepcopy(collection['observed'])
    python_keys = set(map(prior_build.finding_key, python_rows))
    retained_observed['findings'] = [row for row in retained_observed['findings']
                                   if prior_build.finding_key(row) not in python_keys]
    reviewed = reviewed_source_profile(scan, retained_observed, historical, root)
    prior.assess_findings(root, reviewed, retained_observed, retained_observed['compiled'])
    prior.verify_busybox(root, historical)
    from scripts import runtime_ci_inventory
    current.require(inventory == runtime_ci_inventory.build_record(root)
                    and inventory['grafana_selection']['profile'] == current.PROFILE
                    and inventory['grafana_selection']['mode'] == 'source_build',
                    'Actual current Compose/CI selection differs from the new source subject')
    return {'passed': True, 'mode': 'current_source_and_fixed_go_migration_verified',
        'profile': current.PROFILE, 'raw_counts': collection['raw_counts'], 'raw_report_modified': False,
        'unresolved_count': 0, 'verified_all_severity_findings': len(collection['observed']['findings']),
        'historical_source': SOURCE, 'historical_compilation_is_current': False,
        'new_native_graph_migration_verified': True, 'new_native_sha256': profile['expected']['native_sha256'],
        'source_inventory_review': SOURCE_REVIEW, 'source_inventory_review_sha256': SOURCE_REVIEW_SHA256,
        'python_source_review': PYTHON_REVIEW, 'python_source_review_sha256': PYTHON_REVIEW_SHA256,
        'additional_exact_python_not_affected': python_rows,
        'binary_redistribution_approved': False,
        'preserved_original_failed_verdict': original, 'preserved_candidate_failed_verdict': candidate}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('scan', 'scan-receipt', 'original-verdict', 'candidate-verdict', 'runtime-inventory', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink() or not args.output.resolve().is_relative_to(ROOT / '.cache'):
        print(json.dumps({'passed': False, 'error': 'Use a new output under this checkout .cache'}))
        return 1
    try:
        current.require(args.scan.is_file() and not args.scan.is_symlink()
                        and 0 < args.scan.stat().st_size <= prior.old.MAX_REPORT_BYTES,
                        'Complete raw source scan is unavailable')
        scan, receipt, original, candidate, inventory = [prior.old.json_loads(path.read_bytes()) for path in
            (args.scan, args.scan_receipt, args.original_verdict, args.candidate_verdict, args.runtime_inventory)]
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
        dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT)
        result = evaluate(scan, receipt, original, candidate, inventory, raw_sha256=current.digest(args.scan),
            original_sha256=current.digest(args.original_verdict), source_commit=head, tracked_clean=not dirty)
    except (ValueError, KeyError, TypeError, OSError, EOFError, UnicodeError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'mode': 'fixed_go_source_failed_closed', 'error_type': type(error).__name__,
                  'error': str(error)[:240] if isinstance(error, ValueError) else 'Required exact source evidence unavailable'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, indent=2) + '\n').encode())
    print(json.dumps({key: result[key] for key in ('passed', 'mode', 'unresolved_count', 'error') if key in result}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
