"""Cache only an image archive; the original builder always verifies its payload."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ACTION = '.github/actions/reviewed-source-image/action.yml'
SCHEMA = 'reviewed-source-image-v1'


def source_inputs(artifact):
    if artifact == 'minio':
        from scripts.build_minio_os_20261113 import source_inputs as inputs
        return inputs()
    from scripts.build_go_stdlib_security_20261112 import source_inputs as inputs
    return inputs('grafana')


def cache_key(artifact, inputs, helper_bytes, action_bytes):
    identity = {'schema': SCHEMA, 'platform': 'linux/amd64', 'artifact': artifact,
                'source_inputs': inputs, 'helper_sha256': hashlib.sha256(helper_bytes).hexdigest(),
                'action_sha256': hashlib.sha256(action_bytes).hexdigest()}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return SCHEMA + '-' + artifact + '-linux-amd64-' + digest


def builder_command(artifact, output, *, hit):
    command = [sys.executable, '-B', str(ROOT / ('scripts/build_minio_os_20261113.py' if artifact == 'minio'
               else 'scripts/build_go_stdlib_security_20261112.py'))]
    if artifact == 'grafana':
        command += ['--artifact', 'grafana']
    command += ['--output-dir', str(output)]
    if hit:
        command.append('--no-build')
    return command


def execute(artifact, output, archive, *, hit):
    if hit:
        if not archive.is_file() or archive.is_symlink():
            raise ValueError('An exact cache hit requires its regular image archive')
        subprocess.run(['docker', 'image', 'load', '--input', str(archive)], check=True)
    # A corrupt cache or failed current profile is an error, never a cold fallback.
    subprocess.run(builder_command(artifact, output, hit=hit), cwd=ROOT, check=True)
    receipt = json.loads((output / 'build-receipt.json').read_bytes())
    inspection = json.loads((output / 'image-inspect.json').read_bytes())
    if (receipt['preparation_only'] is not False or receipt['image_id'] != inspection['Id']
            or inspection['Os'] != 'linux' or inspection['Architecture'] != 'amd64'
            or receipt['source_inputs'] != source_inputs(artifact)):
        raise ValueError('Verified image receipt differs from the requested cache subject')
    labels = inspection['Config'].get('Labels') or {}
    result = {'schema': SCHEMA, 'exact_cache_hit': hit, 'image_id': receipt['image_id'],
              'portable_config_digest': receipt['config_digest'], 'diff_ids': receipt['diff_ids'],
              'original_builder_current_profile_verified': True,
              'source_revision_label_preserved': labels.get('org.deepresearch.project.source_revision',
                                                           labels.get('org.opencontainers.image.revision')),
              'new_native_compilation_claimed': False, 'original_builder_build_requested': not hit,
              'reuse_statement': 'Existing runtime bytes were loaded and re-exported by the original strict builder.'
                                if hit else 'Original builder build requested; Docker may reuse layers. Archive eligible only after verification.'}
    (output.parent / (output.name + '-cache.json')).write_bytes((json.dumps(result, indent=2) + '\n').encode())
    print(json.dumps(result))


def pack(output, archive):
    source = output / 'actual-payload/image.tar'
    if not source.is_file() or source.is_symlink() or archive.exists():
        raise ValueError('Use the completed verified image export and a new cache archive')
    with source.open('rb') as incoming, archive.open('xb') as target:
        with gzip.GzipFile(filename='', mode='wb', compresslevel=1, fileobj=target, mtime=0) as compressed:
            shutil.copyfileobj(incoming, compressed)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('key', 'run', 'pack'))
    parser.add_argument('--artifact', choices=('minio', 'grafana'), required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--cache-hit', choices=('true', 'false'), default='false')
    args = parser.parse_args(argv)
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT / '.cache') or output.is_symlink():
        raise ValueError('Build output must remain inside the workspace cache')
    directory = ROOT / '.cache/reviewed-source-image-cache' / args.artifact
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / 'image.tar.gz'
    if args.operation == 'key':
        key = cache_key(args.artifact, source_inputs(args.artifact), Path(__file__).read_bytes(),
                        (ROOT / ACTION).read_bytes())
        with Path(os.environ['GITHUB_OUTPUT']).open('a', encoding='utf-8') as stream:
            stream.write('key=' + key + '\narchive=' + str(archive) + '\n')
    elif args.operation == 'run':
        execute(args.artifact, output, archive, hit=args.cache_hit == 'true')
    else:
        pack(output, archive)


if __name__ == '__main__':
    main()
