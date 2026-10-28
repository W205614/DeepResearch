"""Capture complete production Go JSON graphs without applying dispositions."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def packages(path: Path):
    decoder = json.JSONDecoder()
    buffer = ''
    with path.open(encoding='utf-8') as stream:
        complete = False
        while True:
            buffer = buffer.lstrip()
            if buffer:
                try:
                    value, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    if complete:
                        raise ValueError('Incomplete or malformed production graph') from None
                else:
                    yield value
                    buffer = buffer[end:]
                    continue
            elif complete:
                return
            if complete:
                raise ValueError('Incomplete or malformed production graph')
            chunk = stream.read(1024 * 1024)
            complete = not chunk
            buffer += chunk


def capture(go: str, source: Path, package: str, target: str, output: Path, *, gowork_off: bool):
    if not re.fullmatch(r'[a-z0-9_-]+', target) or not source.is_dir():
        raise ValueError('Invalid graph target or source directory')
    output.mkdir(parents=True, exist_ok=True)
    raw_dir = output / 'raw'
    raw_dir.mkdir(exist_ok=True)
    raw = raw_dir / (target + '.json')
    stderr = raw_dir / (target + '.stderr.txt')
    env = dict(os.environ)
    if gowork_off:
        env['GOWORK'] = 'off'
    command = [go, 'list', '-deps', '-json', '-mod=readonly', package]
    with raw.open('wb') as stdout, stderr.open('wb') as errors:
        process = subprocess.run(command, cwd=source, env=env, stdout=stdout, stderr=errors, check=False)
    if process.returncode:
        raise RuntimeError('Production graph command failed for ' + target + '; complete output retained')
    names, module_names = set(), set()
    for row in packages(raw):
        if (not isinstance(row, dict) or not isinstance(row.get('ImportPath'), str)
                or not row['ImportPath'] or row.get('Error') or row.get('DepsErrors') or row.get('Incomplete')):
            raise ValueError('Production graph contains an incomplete package or dependency error')
        if row['ImportPath'] in names:
            raise ValueError('Production graph contains duplicate package identities')
        names.add(row['ImportPath'])
        module = row.get('Module')
        if module:
            module_names.add((module['Path'], module.get('Version')))
    if not names:
        raise ValueError('Production graph is empty')
    compressed = output / (target + '.json.gz')
    with raw.open('rb') as source_stream, compressed.open('wb') as destination:
        with gzip.GzipFile(filename='', mode='wb', fileobj=destination, mtime=0) as compressor:
            shutil.copyfileobj(source_stream, compressor)
    locks = output / 'locks' / target
    locks.mkdir(parents=True, exist_ok=True)
    actual_locks = {}
    for filename in ('go.mod', 'go.sum', 'go.work', 'go.work.sum'):
        path = source / filename
        if path.is_file():
            shutil.copyfile(path, locks / filename)
            actual_locks[filename] = digest(path)
    go_env = subprocess.run([go, 'env', '-json', 'GOOS', 'GOARCH', 'CGO_ENABLED', 'GOAMD64', 'GOFLAGS', 'GOWORK'],
                            cwd=source, env=env, check=True, capture_output=True)
    version = subprocess.run([go, 'version'], cwd=source, env=env, check=True, capture_output=True)
    receipt = {'schema': 'grafana-candidate-complete-production-graph-v1', 'target': target,
               'package': package, 'command': command, 'compiler': version.stdout.decode().strip(),
               'go_environment': json.loads(go_env.stdout), 'completed': True, 'exit_code': process.returncode,
               'raw_bytes': raw.stat().st_size, 'raw_sha256': digest(raw),
               'compressed_bytes': compressed.stat().st_size, 'compressed_sha256': digest(compressed),
               'package_count': len(names), 'module_count': len(module_names), 'actual_lock_sha256': actual_locks,
               'raw_graph_modified_or_filtered': False,
               'boundary': 'Build evidence only; no vulnerability disposition, artifact approval or deployment.'}
    (output / (target + '.receipt.json')).write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'target': target, 'packages': len(names), 'raw_bytes': raw.stat().st_size}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--go', default='go')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--plugins-root', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--package')
    parser.add_argument('--target')
    parser.add_argument('--gowork-off', action='store_true')
    args = parser.parse_args()
    if args.inventory or args.plugins_root:
        if not args.inventory or not args.plugins_root:
            parser.error('Both inventory and plugins-root are required')
        modules = json.loads(args.inventory.read_bytes())
        if len(modules) != 8:
            raise ValueError('Expected exactly eight maintained plugin backends')
        for module in modules:
            directory = module['plugin_directory']
            capture(args.go, args.plugins_root / directory, module['source_package'], directory,
                    args.output, gowork_off=True)
    else:
        if not args.source or not args.package or not args.target:
            parser.error('source, package and target are required for the main graph')
        capture(args.go, args.source, args.package, args.target, args.output, gowork_off=args.gowork_off)


if __name__ == '__main__':
    main()
