"""Fetch the exact public Syft and Grype archives before native CI checks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_native_jvm import scanner_archive_response  # noqa: E402

LOCK = ROOT / 'deployment/security/native-jvm-tools.json'
CACHE = ROOT / '.cache/native-jvm/tools'


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    tools = json.loads(LOCK.read_bytes())['tools']
    for name in ('syft', 'grype'):
        tool = tools[name]
        spec = tool['linux_amd64']
        archive = CACHE / (name + '-' + tool['version'] + '.tar.gz')
        expected_prefix = f'https://github.com/anchore/{name}/releases/download/{tool["version"]}/'
        if not spec['url'].startswith(expected_prefix):
            raise ValueError('Native scanner release URL differs from the official publisher')
        fetched = archive
        if not archive.exists():
            fetched = archive.with_suffix('.release-' + uuid.uuid4().hex + '.download')
            command = ['curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
                       '--retry', '2', '--retry-all-errors', '--max-time', '180',
                       '--max-filesize', str(spec['archive_bytes']), '--silent', '--show-error',
                       spec['url'], '--output', str(fetched)]
            if subprocess.run(command, check=False).returncode:
                fetched = archive.with_suffix('.api-' + uuid.uuid4().hex + '.download')
                with scanner_archive_response(spec) as response, fetched.open('xb') as output:
                    received = 0
                    while block := response.read(1024 * 1024):
                        received += len(block)
                        if received > spec['archive_bytes']:
                            raise ValueError('Native scanner archive exceeds the publisher size: ' + name)
                        output.write(block)
        with fetched.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if fetched.stat().st_size != spec['archive_bytes'] or digest != spec['archive_sha256']:
            raise ValueError('Native scanner release digest or size differs: ' + name)
        if fetched != archive:
            fetched.rename(archive)
        print(name, tool['version'], digest)


if __name__ == '__main__':
    main()
