"""Recheck current source tuples and explicitly migrate historical compiled facts to fixed Go builds."""
from __future__ import annotations

import argparse
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


def evaluate(scan, receipt, original, candidate, inventory, *, raw_sha256, original_sha256,
             source_commit, tracked_clean, root=ROOT):
    from scripts import source_dependency_migration_20261115 as migration
    from scripts import runtime_ci_inventory
    historical_root = snapshot(root)
    historical = prior_build.load_profile(historical_root)
    result = migration.collect(scan, receipt, original, candidate,
        raw_sha256=raw_sha256, original_sha256=original_sha256,
        source_commit=source_commit, tracked_clean=tracked_clean,
        historical=historical, historical_root=historical_root, root=root)
    current.require(inventory == runtime_ci_inventory.build_record(root)
                    and inventory['grafana_selection']['profile'] == current.PROFILE
                    and inventory['grafana_selection']['mode'] == 'source_build',
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
