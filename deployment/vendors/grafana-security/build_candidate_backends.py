"""Fill checksums with default SumDB before the provenance-checked candidate build."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess


def fingerprint(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def prepare_checksums(source: Path, receipt: Path, go: str = 'go'):
    module, sums = source / 'go.mod', source / 'go.sum'
    before_mod, before_sum = fingerprint(module), fingerprint(sums)
    if before_mod is None:
        raise ValueError('Missing declared go.mod before checksum preparation')
    sumdb = subprocess.run([go, 'env', 'GOSUMDB'], cwd=source, check=True, capture_output=True, text=True).stdout.strip()
    if sumdb != 'sum.golang.org':
        raise ValueError('Candidate checksum preparation requires the default Go SumDB')
    result = subprocess.run([go, 'mod', 'download'], cwd=source, check=False)
    after_mod, after_sum = fingerprint(module), fingerprint(sums)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    record = {'schema': 'grafana-candidate-checksum-preparation-v1', 'command': [go, 'mod', 'download'],
              'sumdb': sumdb, 'exit_code': result.returncode, 'declared_go_mod_sha256': before_mod,
              'actual_go_mod_sha256': after_mod, 'go_mod_unchanged': before_mod == after_mod,
              'declared_go_sum_sha256': before_sum, 'actual_go_sum_sha256': after_sum,
              'boundary': 'Checksum preparation only; no dependency version update or vulnerability disposition.'}
    receipt.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    if result.returncode or before_mod != after_mod:
        raise ValueError('Checksum preparation failed or changed the fixed go.mod')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--locks', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--vcs-cache', type=Path)
    parser.add_argument('--builder', type=Path, default=Path('/tools/candidate_build_backends.py'))
    parser.add_argument('--prepare-source', type=Path)
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--go', default='go')
    args = parser.parse_args()
    if args.prepare_source:
        if not args.receipt:
            parser.error('receipt is required with prepare-source')
        prepare_checksums(args.prepare_source, args.receipt, args.go)
        return
    if not all((args.inventory, args.locks, args.output)):
        parser.error('inventory, locks and output are required for backend builds')
    spec = importlib.util.spec_from_file_location('candidate_grafana_backend_builder', args.builder)
    if spec is None or spec.loader is None:
        raise ValueError('Candidate backend builder is unavailable')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    original_run = builder.run

    def guarded_run(command, cwd, timeout=900):
        if command[:2] == ['go', 'build']:
            prepare_checksums(Path(cwd), args.output / 'checksum-preparation' / (Path(cwd).name + '.json'))
        return original_run(command, cwd, timeout)

    builder.run = guarded_run
    builder.build(args.inventory, args.locks, args.output, args.vcs_cache)


if __name__ == '__main__':
    main()
