"""Export actual Tempo build identity for independent review, without a waiver."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def run(args):
    return subprocess.check_output([str(value) for value in args], stderr=subprocess.PIPE)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    image = json.loads(run(['docker', 'image', 'inspect', args.image]))[0]
    (args.output_dir / 'image-inspect.json').write_text(json.dumps(image, indent=2), encoding='utf-8')
    container = run(['docker', 'create', image['Id']]).decode().strip()
    if not re.fullmatch('[0-9a-f]{64}', container):
        raise ValueError('Unexpected build-inspection container identity')
    binary = args.output_dir / 'tempo.elf'
    try:
        run(['docker', 'cp', container + ':/tempo', binary])
    finally:
        actual = json.loads(run(['docker', 'inspect', container]))[0]
        if actual['Image'] != image['Id'] or actual['State']['Status'] != 'created':
            raise ValueError('Fresh evidence-only container state changed')
        run(['docker', 'rm', container])
    native = run(['go', 'version', '-m', '-json', binary])
    json.loads(native)
    (args.output_dir / 'native-buildinfo.json').write_bytes(native)
    (args.output_dir / 'native-buildinfo.txt').write_bytes(run(['go', 'version', '-m', binary]))
    version = run(['docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                   '--entrypoint', '/tempo', image['Id'], '-version'])
    (args.output_dir / 'runtime-version.txt').write_bytes(version)
    record = {'schema': 'tempo-actual-build-materials-v1', 'security_gate_claim': False,
              'technical_disposition': 'NOT_DECIDED', 'actual_image_id': image['Id'],
              'actual_diff_ids': image['RootFS']['Layers'], 'elf_bytes': binary.stat().st_size,
              'elf_sha256': digest(binary), 'native_buildinfo_sha256': digest(args.output_dir / 'native-buildinfo.json'),
              'runtime_version_sha256': digest(args.output_dir / 'runtime-version.txt')}
    (args.output_dir / 'materials.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(record))


if __name__ == '__main__':
    main()
