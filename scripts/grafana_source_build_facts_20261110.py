"""Read immutable image bytes through an owned, never-started isolated container."""
from pathlib import Path
import gzip
import hashlib
import json
import subprocess
import tarfile
import uuid

AUTO_FILES = {'etc/hostname', 'etc/hosts', 'etc/resolv.conf', '.dockerenv'}


def run(args):
    return subprocess.check_output(args)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def capture(image, output, *, save_config=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    inspection, = json.loads(run(['docker', 'image', 'inspect', image]))
    name = 'dr-grafana-source-facts-' + uuid.uuid4().hex[:12]
    container = run(['docker', 'create', '--name', name, '--network', 'none',
        '--label', 'com.docker.compose.project=' + name, inspection['Id']]).decode().strip()
    try:
        created, = json.loads(run(['docker', 'inspect', container]))
        if (created['Name'] != '/' + name or created['Image'] != inspection['Id']
                or created['State']['Running'] or created['HostConfig']['NetworkMode'] != 'none'
                or any(m['Type'] == 'bind' for m in created['Mounts'])):
            raise ValueError('Isolated image fact container identity differs')
        archive = output / 'rootfs.tar'
        subprocess.run(['docker', 'export', '--output', str(archive), container], check=True)
        rows = []
        with tarfile.open(archive) as source:
            for member in source:
                path = member.name.removeprefix('./').rstrip('/')
                if not path or path in AUTO_FILES:
                    continue
                if path.startswith('/') or '..' in Path(path).parts:
                    raise ValueError('Unsafe image payload member')
                row = {'path': path, 'mode': member.mode, 'uid': member.uid, 'gid': member.gid}
                if member.isfile():
                    with source.extractfile(member) as stream:
                        hashed = hashlib.file_digest(stream, 'sha256').hexdigest()
                    row.update(type='file', bytes=member.size, sha256=hashed)
                elif member.isdir():
                    row['type'] = 'directory'
                elif member.issym() or member.islnk():
                    row.update(type='symlink' if member.issym() else 'hardlink', target=member.linkname)
                else:
                    raise ValueError('Unexpected special file in inactive image payload')
                rows.append(row)
        rows.sort(key=lambda r: r['path'])
        if len({r['path'] for r in rows}) != len(rows):
            raise ValueError('Ambiguous duplicate image payload members')
        with (output / 'runtime-manifest.json.gz').open('wb') as stream:
            with gzip.GzipFile(fileobj=stream, mode='wb', filename='', mtime=0) as packed:
                packed.write(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode())
        result = {'image_id': inspection['Id'], 'inspection': inspection,
            'diff_ids': inspection['RootFS']['Layers'], 'runtime_manifest_sha256': canonical_hash(rows),
            'runtime_manifest': rows, 'own_container': container,
            'auto_files_excluded': sorted(AUTO_FILES), 'container_started': False}
        if save_config:
            saved = output / 'image.tar'
            subprocess.run(['docker', 'image', 'save', '--output', str(saved), image], check=True)
            with tarfile.open(saved) as source:
                entry, = json.load(source.extractfile('manifest.json'))
                raw = source.extractfile(entry['Config']).read()
            config = json.loads(raw)
            if config['rootfs']['diff_ids'] != result['diff_ids']:
                raise ValueError('Portable config differs from actual exported image')
            result['config_digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
            (output / 'image-config.json').write_bytes(raw)
        after, = json.loads(run(['docker', 'image', 'inspect', image]))
        if after['Id'] != inspection['Id']:
            raise ValueError('Image tag changed during evidence capture')
        return result
    finally:
        current, = json.loads(run(['docker', 'inspect', container]))
        if (current['Name'] != '/' + name or current['Image'] != inspection['Id']
                or current['State']['Running'] or current['HostConfig']['NetworkMode'] != 'none'
                or any(m['Type'] == 'bind' for m in current['Mounts'])):
            raise ValueError('Owned fact container changed; preserved for inspection')
        subprocess.run(['docker', 'rm', '-v', container], check=True, stdout=subprocess.DEVNULL)
