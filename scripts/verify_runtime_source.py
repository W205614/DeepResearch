"""Verify built input manifests against actual container bytes and the checkout."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def capture(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('Runtime source inspection failed; private output withheld')
    return result.stdout


def parse_manifest(value):
    output = {}
    for line in value.decode().splitlines():
        digest, filename = line.split(maxsplit=1)
        filename = filename.lstrip('* ')
        if PurePosixPath(filename).is_absolute() or '..' in PurePosixPath(filename).parts:
            raise ValueError('Invalid build manifest path')
        output[filename] = digest
    return output


def compare(manifest, base):
    different = []
    for name, digest in manifest.items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid source manifest path')
        path = base.joinpath(*relative.parts)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            different.append(name)
    return different


def verify(project):
    if project not in {'deepresearch', 'dr-opt-acceptance'}:
        raise ValueError('Explicit owned deployment required')
    ids = capture(['docker', 'ps', '-q', '--filter', 'label=com.docker.compose.project=' + project]).decode().split()
    rows = []
    for container in ids:
        data = json.loads(capture(['docker', 'inspect', container]))[0]
        service = data['Config']['Labels'].get('com.docker.compose.service')
        if service not in {'agent', 'worker', 'document-worker', 'backend', 'web'}:
            continue
        if not data['Config']['Labels'].get('com.docker.compose.config-hash'):
            continue
        result = {'service': service, 'container_id': data['Id'], 'image_id': data['Image']}
        if service in {'agent', 'worker', 'document-worker'}:
            code = "import pathlib,json,hashlib; r=pathlib.Path('/app'); m=json.loads((r/'source-fingerprint.json').read_text()); a={k:hashlib.sha256((r/k).read_bytes()).hexdigest() for k in m}; print(json.dumps({'built':m,'actual':a}))"
            manifest = json.loads(capture(['docker', 'exec', container, 'python', '-c', code]))
            result['container_files_modified'] = [name for name, digest in manifest['built'].items() if manifest['actual'][name] != digest]
            result['checkout_differences'] = compare(manifest['built'], ROOT)
            result['input_files'] = len(manifest['built'])
        else:
            path = '/app/source-fingerprint.txt' if service == 'backend' else '/etc/deepresearch/source-fingerprint.txt'
            manifest = parse_manifest(capture(['docker', 'exec', container, 'cat', path]))
            result['checkout_differences'] = compare(manifest, ROOT / ('business-backend' if service == 'backend' else 'frontend'))
            result['input_files'] = len(manifest)
            if service == 'backend':
                result['jar_sha256'] = capture(['docker', 'exec', container, 'sha256sum', '/app/app.jar']).decode().split()[0]
            else:
                dist = parse_manifest(capture(['docker', 'exec', container, 'cat', '/etc/deepresearch/dist-fingerprint.txt']))
                targets = ['/usr/share/nginx/html/' + name.removeprefix('dist/') for name in dist]
                actual = capture(['docker', 'exec', container, 'sha256sum', *targets]).decode().splitlines()
                values = {name: line.split()[0] for name, line in zip(dist, actual)}
                result['dist_files'] = len(dist)
                result['container_files_modified'] = [name for name, digest in dist.items() if values[name] != digest]
        result['passed'] = not result['checkout_differences'] and not result.get('container_files_modified')
        rows.append(result)
    return {'project': project, 'passed': len(rows) == 5 and all(row['passed'] for row in rows), 'services': rows,
            'fixture_mounts': 'isolated component providers are explicit test-only mounts outside production input manifests' if project == 'dr-opt-acceptance' else None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = verify(args.project)
    with Path(args.output).open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, indent=2))
    print(json.dumps(report))
    raise SystemExit(0 if report['passed'] else 1)
