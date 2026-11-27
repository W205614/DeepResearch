"""Verify current source findings against the retained compiled and source-only proofs."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_scan_20261110 as prior  # noqa: E402
from scripts import check_grafana_source_build_20261110 as prior_build  # noqa: E402
from scripts import check_grafana_source_build_20261114 as current  # noqa: E402
from scripts.retained_source_review_20261112 import snapshot  # noqa: E402
from scripts import go_lock_upgrade_20261122 as lock_upgrade  # noqa: E402


from scripts.source_dependency_migration_20261115 import (  # noqa: E402
    CONTEXTS_SHA256, MINIO_TARGET, REVIEW, REVIEW_SHA256, checked_review,
    verify_current_proofs, verify_minio_findings, verify_observed,
)


BACKEND_INVENTORY_REVIEW = 'deployment/security/backend-jetty-source-inventory-20261127.json'
BACKEND_INVENTORY_REVIEW_SHA256 = '568958dc206ba8065080cb38fb7c6721232d19ab8c4653c55afe6822dd0839d9'
SOURCE_ONLY_FINDINGS = {
    ('CVE-2026-95839', 'deployment/vendors/loki/go.mod', 'github.com/containerd/containerd/v2', 'v2.2.9', 'HIGH'):
        ('loki',),
    **{(identifier, MINIO_TARGET, 'github.com/nats-io/nats-server/v2', 'v2.11.15', severity):
       ('minio_silo', 'minio_mcli') for identifier, severity in (
           ('CVE-2026-58208', 'MEDIUM'), ('CVE-2026-58209', 'MEDIUM'), ('CVE-2026-58210', 'HIGH'),
           ('CVE-2026-58213', 'HIGH'), ('CVE-2026-58214', 'MEDIUM'), ('CVE-2026-58254', 'MEDIUM'))},
}


def collect(scan, receipt, original, candidate, *, raw_sha256, original_sha256,
            source_commit, tracked_clean, historical, historical_root, root):
    lock_upgrade.verify_locks(root)
    previous_root = lock_upgrade.previous_source(root)
    review = checked_review(previous_root)
    current.require(review['contexts_sha256'] == CONTEXTS_SHA256, 'Source finding and context reviews differ')
    observed, counts, findings = prior.source_facts(scan)
    current.require(not any(prior_build.finding_key(row) in SOURCE_ONLY_FINDINGS for row in observed['findings']),
                    'An upgraded Go source dependency still reports its original alert')
    backend_review = prior_build.checked(root, BACKEND_INVENTORY_REVIEW)
    current.require(current.digest(backend_review) == BACKEND_INVENTORY_REVIEW_SHA256,
                    'Reviewed Backend source inventory changed')
    backend = prior.old.json_loads(backend_review.read_bytes())
    inventories = review['target_inventory_sha256'] | lock_upgrade.SCAN_INVENTORIES
    target = backend['target']
    current.require(current.digest(prior_build.checked(root, target)) == backend['pom_sha256']
                    and inventories[target] == backend['previous_inventory_sha256'],
                    'The Backend POM or preceding exact inventory differs')
    inventories[target] = backend['inventory_sha256']
    expected = {**review, 'target_inventory_sha256': inventories,
                'findings': [row for row in review['findings']
                             if prior_build.finding_key(row) not in SOURCE_ONLY_FINDINGS]}
    verify_observed(observed, expected)
    now = datetime.now(timezone.utc)
    _, scope, _ = prior.old.load_scope(root, prior.old.POLICY, now)
    scope = deepcopy(scope)
    target = next(r for r in scan['Results'] if r['Target'] == scope['source_path'])
    packages = prior.old.inventory(target['Packages'])
    scope['source_inventory_count'] = len(packages)
    scope['source_inventory_sha256'] = hashlib.sha256(prior.old.canonical(packages)).hexdigest()
    expected_targets = prior.old.committed_manifest_targets(root, source_commit)
    prior.old.validate_completion(scan, receipt, raw_sha256, scope, source_commit,
        tracked_clean, findings, now, expected_targets=expected_targets)
    recomputed = prior.old.evaluate(scan, receipt, raw_sha256=raw_sha256,
        source_commit=source_commit, tracked_clean=tracked_clean, root=root, now=now,
        expected_targets=expected_targets)
    current.require(recomputed == original, 'Original failed verdict differs from recomputation')
    recomputed_candidate = prior.candidate.evaluate(scan, receipt, original,
        raw_sha256=raw_sha256, source_commit=source_commit, tracked_clean=tracked_clean,
        root=root, now=now, expected_targets=expected_targets)
    current.require({k: v for k, v in candidate.items()
                     if k != 'preserved_original_verdict_file_sha256'} == recomputed_candidate
                    and candidate['preserved_original_verdict_file_sha256'] == original_sha256,
                    'Candidate failed verdict differs from recomputation')
    compiled = prior.verify_compiled(historical_root, historical)
    legacy = historical['expected']['source_scan']
    retained_keys = set(map(prior_build.finding_key, legacy['reviewed_findings']))
    actual_keys = set(map(prior_build.finding_key, observed['findings']))
    current.require(retained_keys <= actual_keys, 'An original reviewed finding disappeared or changed')
    retained = {'scan_targets': legacy['scan_targets'],
                'target_inventory_sha256': legacy['target_inventory_sha256'],
                'findings': legacy['reviewed_findings']}
    # This separate projection validates the old immutable compilation; the raw
    # current scan and its full inventories above are never rewritten.
    prior.assess_findings(historical_root, historical, retained, compiled)
    prior.verify_busybox(historical_root, historical)
    new = [row for row in observed['findings'] if prior_build.finding_key(row) not in retained_keys]
    minio_rows = [row for row in new if row['target'] == MINIO_TARGET]
    new = [row for row in new if row['target'] != MINIO_TARGET]
    current.require(new == review['additional_historical_otel_findings']
                    and minio_rows == review['additional_minio_findings'],
                    'Additional source findings exceed the exact reviewed migration scope')
    minio_proofs = verify_minio_findings(previous_root, minio_rows, review)
    profile = current.load_profile(root)
    current_proofs = verify_current_proofs(previous_root, historical, profile)
    for row in new:
        current.require(row['id'] == 'CVE-2026-128085' and row['severity'] == 'MEDIUM'
                        and row['target'].startswith('deployment/security/')
                        and row['package'].startswith('go.opentelemetry.io/otel/'),
                        'Only the retained historical otel findings have a migration proof')
        target_name = 'main'
        if '/plugins/' in row['target']:
            target_name = row['target'].split('/plugins/', 1)[1].split('/', 1)[0]
        paths = [name for name in profile['native_evidence']
                 if name == prior_build.MAIN and target_name == 'main'
                 or '/plugins-bundled/' + target_name + '/' in name]
        current.require(len(paths) == 1, 'Current executable migration fact is ambiguous')
        evidence = profile['native_evidence'][paths[0]]
        native = json.loads(prior_build.checked(root, evidence['native']).read_bytes())
        modules = {m['Path']: m for m in [native['Main'], *native.get('Deps', [])]}
        module = modules.get(row['package'])
        current.require(module is None or module['Version'] == 'v1.47.0',
                        'Affected otel module remains in the current executable')
    return {'passed': True, 'mode': 'exact_source_dependency_migration_verified',
            'unresolved_count': 0, 'raw_counts': counts,
            'verified_all_severity_findings': len(observed['findings']),
            'additional_historical_otel_findings': new,
            'current_retained_proofs': current_proofs,
            'additional_minio_findings': minio_rows, 'current_minio_source_proofs': minio_proofs,
            'upgraded_source_dependency_alerts_absent': list(SOURCE_ONLY_FINDINGS),
            'raw_report_modified': False, 'historical_compilation_is_current': False,
            'source_dependency_review': REVIEW, 'source_dependency_review_sha256': REVIEW_SHA256,
            'preserved_original_failed_verdict': original,
            'preserved_candidate_failed_verdict': candidate,
            'binary_redistribution_approved': False}


def evaluate(scan, receipt, original, candidate, inventory, *, raw_sha256, original_sha256,
             source_commit, tracked_clean, root=ROOT):
    from scripts import runtime_ci_inventory
    historical_root = snapshot(root)
    historical = prior_build.load_profile(historical_root)
    result = collect(scan, receipt, original, candidate,
        raw_sha256=raw_sha256, original_sha256=original_sha256,
        source_commit=source_commit, tracked_clean=tracked_clean,
        historical=historical, historical_root=historical_root, root=root)
    current.require(inventory == runtime_ci_inventory.build_record(root)
                    and inventory['grafana_selection']['profile'] == runtime_ci_inventory.GRAFANA_1326_PROFILE
                    and inventory['grafana_selection']['mode'] == 'source_build'
                    and inventory['grafana_selection']['retained_backend_source']['build_profile'] == current.PROFILE,
                    'Actual current Compose/CI selection differs from the new source subject')
    return result


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
