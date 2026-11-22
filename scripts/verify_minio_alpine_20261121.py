"""Exercise the MinIO Alpine entrypoint and an isolated S3 round trip."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_minio_alpine_20261121 import IMAGE  # noqa: E402
from scripts.build_minio_otel_20261115 import IMAGE as SOURCE_IMAGE  # noqa: E402


def docker(*args, data=None, check=True):
    return subprocess.run(['docker', *args], input=data, capture_output=True, check=check)


def main():
    name = 'dr-minio-alpine-test-' + uuid.uuid4().hex[:12]
    image = docker('image', 'inspect', '--format', '{{.Id}}', IMAGE).stdout.decode().strip()
    source_image = docker('image', 'inspect', '--format', '{{.Id}}', SOURCE_IMAGE).stdout.decode().strip()
    key, secret = 'isolated-fixture-admin', 'isolated-fixture-secret'
    result = {'image_id': image, 'source_image_id': source_image}
    docker('volume', 'create', name)
    try:
        def ready():
            for _ in range(45):
                if docker('exec', name, 'curl', '-fsS', 'http://127.0.0.1:9000/minio/health/ready',
                          check=False).returncode == 0:
                    return
                time.sleep(1)
            raise RuntimeError('MinIO readiness timed out')

        def start(ref):
            docker('run', '-d', '--name', name, '--network', 'none', '--tmpfs', '/data', '--mount',
                   f'type=volume,source={name},target=/minio_data', '-e', f'MINIO_ROOT_USER={key}',
                   '-e', f'MINIO_ROOT_PASSWORD={secret}', ref, 'server', '/minio_data', '--address', ':9000')
            ready()
            docker('exec', name, 'mcli', 'alias', 'set', 'fixture', 'http://127.0.0.1:9000', key, secret)

        start(source_image)
        docker('exec', name, 'mcli', 'mb', 'fixture/acceptance')
        legacy = b'object written by the previous MinIO runtime\n'
        legacy_name = 'fixture/acceptance/preexisting'
        docker('exec', '-i', name, 'mcli', 'pipe', legacy_name, data=legacy)
        docker('rm', '-f', name)
        start(image)
        if docker('exec', name, 'mcli', 'cat', legacy_name).stdout != legacy:
            raise RuntimeError('MinIO could not read the existing S3 object')
        result['legacy_volume_read'] = True

        payload = b'deepresearch isolated storage round trip\n'
        object_name = 'fixture/acceptance/payload'
        docker('exec', '-i', name, 'mcli', 'pipe', object_name, data=payload)
        docker('restart', name)
        ready()
        if docker('exec', name, 'mcli', 'cat', object_name).stdout != payload:
            raise RuntimeError('MinIO object differed after restart')
        result['s3_write_read_after_restart'] = True

        identity = ['-e', 'MINIO_USERNAME=audit', '-e', 'MINIO_GROUPNAME=audit']
        for mode, ids in [('numeric', ['-e', 'MINIO_UID=1000', '-e', 'MINIO_GID=1000']), ('named', [])]:
            base = ('run', '--rm', '--network', 'none', *identity, *ids, image)
            uid = docker(*base, 'id', '-u').stdout.strip()
            gid = docker(*base, 'id', '-g').stdout.strip()
            if (uid, gid) != (b'1000', b'1000'):
                raise RuntimeError(f'MinIO {mode} user/group switch failed')
            result[f'{mode}_uid_gid'] = [int(uid), int(gid)]
        print(json.dumps(result))
    finally:
        docker('rm', '-f', name, check=False)
        docker('volume', 'rm', name)


if __name__ == '__main__':
    main()
