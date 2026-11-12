"""Prepare official module metadata and export cache before unchanged Go capture."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from capture_graphs import capture


PLUGINS = {'mysql', 'stackdriver', 'mssql', 'jaeger', 'influxdb', 'loki',
           'opentsdb', 'tempo', 'elasticsearch', 'grafana-postgresql-datasource',
           'prometheus', 'zipkin', 'grafana-pyroscope-datasource'}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(path: Path) -> list[dict]:
    rows = json.loads(path.read_bytes())
    if len(rows) != 13 or {row['plugin_directory'] for row in rows} != PLUGINS:
        raise ValueError('Expected exactly thirteen maintained plugin backends')
    for row in rows:
        if (not re.fullmatch(r'github\.com/grafana/[a-z0-9_-]+', row['module'])
                or not re.fullmatch(r'v[0-9a-z.+-]+', row['version'])
                or not re.fullmatch(r'[0-9a-f]{40}', row['commit'])):
            raise ValueError('Invalid pinned module identity')
    return rows


def regular_within(path: Path, root: Path) -> Path:
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Go metadata path is not a regular file inside its module cache')
    return path


def download(row: dict, cache: Path, evidence: Path, phase: str, *, official: bool = True) -> dict:
    env = dict(os.environ, GOMODCACHE=str(cache), GOWORK='off')
    if official:
        env['GOPROXY'] = 'https://proxy.golang.org'
    if subprocess.check_output(['go', 'env', 'GOSUMDB'], env=env).decode().strip() != 'sum.golang.org':
        raise ValueError('Official Go checksum database must remain enabled')
    command = ['go', 'mod', 'download', '-json', row['module'] + '@' + row['version']]
    with (evidence / (phase + '.json')).open('wb') as stdout, (evidence / (phase + '.stderr.txt')).open('wb') as stderr:
        subprocess.run(command, cwd='/tmp', env=env, stdout=stdout, stderr=stderr, check=True)
    result = json.loads((evidence / (phase + '.json')).read_bytes())
    if (result.get('Error') or result['Path'] != row['module'] or result['Version'] != row['version']
            or result['Sum'] != row['source_sum'] or result['GoModSum'] != row['source_mod_sum']
            or result['Origin']['VCS'] != 'git' or result['Origin']['Hash'] != row['commit']
            or result['Origin']['URL'] != 'https://' + row['module']):
        raise ValueError('Official module identity, origin or checksum differs from pinned inventory')
    regular_within(Path(result['Info']), cache)
    return result


def metadata(rows: list[dict], evidence: Path) -> None:
    shared = Path(subprocess.check_output(['go', 'env', 'GOMODCACHE']).decode().strip()).resolve()
    fresh_root = Path(tempfile.mkdtemp(prefix='grafana-official-info-', dir='/tmp'))
    for row in rows:
        name = row['plugin_directory']
        destination = evidence / name
        destination.mkdir(parents=True)
        relative = Path('cache/download') / row['module'] / '@v'
        fresh = fresh_root / name
        transport = fresh / relative
        transport.mkdir(parents=True)
        current = shared / relative
        for suffix in ('mod', 'zip'):
            source = current / (row['version'] + '.' + suffix)
            if source.exists():
                shutil.copyfile(regular_within(source, shared), transport / source.name)
            else:
                prefetched = Path('/prefetched-proxy') / row['module'] / '@v' / source.name
                if prefetched.exists():
                    shutil.copyfile(regular_within(prefetched, Path('/prefetched-proxy')), transport / source.name)
        old_info = current / (row['version'] + '.info')
        if old_info.exists():
            shutil.copyfile(regular_within(old_info, shared), destination / 'prior-shared.info.json')
        result = download(row, fresh, destination, 'official-download')
        info = Path(result['Info'])
        actual = json.loads(info.read_bytes())
        if actual['Version'] != row['version'] or actual['Origin'] != result['Origin']:
            raise ValueError('Actual official module info differs from Go download receipt')
        shutil.copyfile(info, destination / 'actual-go.info.json')
        current.mkdir(parents=True, exist_ok=True)
        if not current.resolve().is_relative_to(shared):
            raise ValueError('Shared metadata destination escaped module cache')
        temporary = current / (row['version'] + '.info.deepresearch-tmp')
        with temporary.open('xb') as stream:
            stream.write(info.read_bytes())
        os.replace(temporary, old_info)
        verified = download(row, shared, destination, 'shared-download', official=False)
        if verified['Origin'] != result['Origin'] or old_info.read_bytes() != info.read_bytes():
            raise ValueError('Shared module cache did not preserve complete official metadata')
        (destination / 'receipt.json').write_text(json.dumps({
            'module': row['module'], 'version': row['version'], 'origin': result['Origin'],
            'source_sum': result['Sum'], 'source_mod_sum': result['GoModSum'],
            'actual_info_sha256': digest(info), 'copied_complete_go_produced_info': True,
            'go_sumdb': 'sum.golang.org', 'metadata_fields_modified_or_filtered': False,
        }, indent=2) + '\n', encoding='utf-8')


def locks(source: Path, nested: list[str]) -> dict[str, str]:
    names = ['go.mod', 'go.sum', 'go.work', 'go.work.sum']
    names += [relative + '/' + name for relative in nested for name in ('go.mod', 'go.sum')]
    return {name: digest(source / name) for name in names if (source / name).is_file()}


def prime_capture(source: Path, package: str, target: str, output: Path, evidence: Path,
                  *, plugin: bool, nested: list[str]) -> None:
    evidence.mkdir(parents=True)
    before = locks(source, nested)
    env = dict(os.environ)
    if plugin:
        env['GOWORK'] = 'off'
    command = ['go', 'list', '-deps', '-export', '-mod=readonly']
    if plugin:
        command.append('-tags=arrow_json_stdlib')
    command.append(package)
    with (evidence / 'export.stdout.txt').open('wb') as stdout, (evidence / 'export.stderr.txt').open('wb') as stderr:
        subprocess.run(command, cwd=source, env=env, stdout=stdout, stderr=stderr, check=True)
    if locks(source, nested) != before:
        raise ValueError('Readonly export preparation changed source lock files')
    capture('go', source, package, target, output, gowork_off=plugin,
            build_tags='arrow_json_stdlib' if plugin else None, nested_modules=nested)
    if locks(source, nested) != before:
        raise ValueError('Production graph capture changed source lock files')
    (evidence / 'receipt.json').write_text(json.dumps({
        'command': command, 'exit_code': 0, 'actual_lock_sha256': before,
        'original_capture_helper_unmodified': True, 'graph_modified_or_filtered': False,
    }, indent=2) + '\n', encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('metadata', 'plugins', 'main'))
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.mode == 'metadata':
        metadata(inventory(args.inventory), args.evidence)
    elif args.mode == 'plugins':
        for row in inventory(args.inventory):
            name = row['plugin_directory']
            prime_capture(Path('/source-plugins') / name, row['source_package'], name,
                          args.output, args.evidence / name, plugin=True,
                          nested=row.get('nested_modules', []))
    else:
        prime_capture(Path('/source'), './pkg/cmd/grafana', 'main', args.output,
                      args.evidence, plugin=False, nested=[])


if __name__ == '__main__':
    main()
