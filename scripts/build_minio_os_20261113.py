"""Build and verify the separate MinIO native/Thrift/curl repair from fixed source inputs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import build_go_stdlib_security_20261112 as baseline  # noqa: E402
from scripts.grafana_source_build_facts_20261110 import capture  # noqa: E402
from scripts.minio_rpm_facts_20261113 import database_facts  # noqa: E402
from scripts.prefetch_minio_os_inputs_20261113 import LOCK, fetch, verify  # noqa: E402

IMAGE = 'deepresearch-minio-source:20261113'
DOCKERFILE = 'deployment/docker/Dockerfile.minio-20261113'
PROFILE = 'minio-os-source-20261113'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def logged(command, output, name):
    write(output / (name + '.command.json'), [str(v) for v in command])
    with (output / (name + '.log')).open('wb') as stream:
        subprocess.run([str(v) for v in command], cwd=ROOT, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)


def source_inputs(root=ROOT):
    names = set(baseline.source_inputs('minio', root))
    names.update({DOCKERFILE, DOCKERFILE + '.dockerignore',
                  'scripts/build_minio_os_20261113.py', 'scripts/prefetch_minio_os_inputs_20261113.py',
                  'scripts/minio_rpm_facts_20261113.py', 'scripts/collect_minio_os_native_20261113.py',
                  'deployment/security/minio-source-20261112-profile.json'})
    names.update(p.relative_to(root).as_posix()
                 for p in (root / 'deployment/security/minio-os-20261113').rglob('*') if p.is_file())
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}


def build(args):
    output = args.output_dir.resolve()
    if output.exists() or output.is_symlink() or not output.is_relative_to(ROOT / '.cache'):
        raise ValueError('Use a new build directory below the workspace cache')
    output.mkdir(parents=True)
    inputs = source_inputs()
    profile = None
    if not args.prepare:
        from scripts import check_minio_os_20261113 as gate
        profile = gate.load_profile()
    if not args.no_build:
        lock = json.loads(LOCK.read_bytes())
        input_dir = args.input_dir or ROOT / '.cache/minio-os-20261113/pinned-inputs'
        write(output / 'input-verification.json',
              verify(input_dir, lock) if args.input_dir else fetch(input_dir, lock))
        transport_args = argparse.Namespace(artifact='minio', module_transport=args.module_transport,
                                            source_repository=args.source_repository)
        contexts = baseline.prepare_contexts(transport_args, output / 'go-source-inputs')
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        command = ['docker', 'buildx', 'build', '--pull=false', '--progress', 'plain',
                   '--build-context', 'native-inputs=' + str(input_dir.resolve()),
                   '--build-arg', 'PROJECT_SOURCE_REVISION=' + revision,
                   '--file', ROOT / DOCKERFILE]
        for name, path in contexts.items():
            command += ['--build-context', name + '=' + str(path.resolve())]
        if args.prepare:
            command += ['--build-arg', 'TRANSACTION_OBSERVATION=prepare-' + uuid.uuid4().hex]
        logged([*command, '--target', 'candidate', '--load', '-t', IMAGE, ROOT], output, 'build')
        logged([*command, '--target', 'build-evidence', '--output',
                'type=local,dest=' + str(output / 'build-evidence'), ROOT], output, 'build-evidence')
    facts = capture(IMAGE, output / 'actual-payload', save_config=True)
    if inputs != source_inputs():
        raise ValueError('Reviewed input bytes changed during build/capture')
    files = {row['path']: row for row in facts['runtime_manifest']}
    database = output / 'rpmdb.sqlite'
    with tarfile.open(output / 'actual-payload/rootfs.tar') as archive:
        member = archive.getmember('var/lib/rpm/rpmdb.sqlite')
        if not member.isfile():
            raise ValueError('Actual RPM database is not a regular payload file')
        database.write_bytes(archive.extractfile(member).read())
    rpm = database_facts(database)
    receipt = {'schema': 'minio-os-source-build-receipt-20261113-v1', 'profile': PROFILE,
               'image': IMAGE, 'image_id': facts['image_id'], 'config_digest': facts['config_digest'],
               'diff_ids': facts['diff_ids'], 'source_inputs': inputs,
               'runtime_manifest_sha256': facts['runtime_manifest_sha256'],
               'runtime_config': baseline.old.runtime_config(facts['inspection']['Config']),
               'native_sha256': {name: files[name]['sha256'] for name in
                                 ('usr/bin/mcli', 'usr/bin/silo', 'usr/bin/curl', 'usr/lib64/libc.so.6',
                                  'usr/lib64/ld-linux-x86-64.so.2', 'usr/lib64/libtic.so.6.2')},
               'rpm_database': {k: v for k, v in rpm.items() if k != 'stable'},
               'operation': 'verify-existing-image' if args.no_build else 'build-and-verify',
               'build_executed': not args.no_build,
               'observed_at_utc': datetime.now(timezone.utc).isoformat(),
               'preparation_only': args.prepare, 'binary_redistribution_approved': False}
    write(output / 'build-receipt.json', receipt)
    write(output / 'image-inspect.json', facts['inspection'])
    if profile is not None:
        gate.verify_receipt(receipt, facts['inspection'], profile)
        gate.verify_payload(facts, database, profile, receipt=receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.cache/minio-os-20261113' / uuid.uuid4().hex)
    parser.add_argument('--input-dir', type=Path)
    parser.add_argument('--source-repository', type=Path)
    parser.add_argument('--module-transport', type=Path)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--no-build', action='store_true')
    args = parser.parse_args()
    receipt = build(args)
    print(json.dumps({'image_id': receipt['image_id'], 'preparation_only': args.prepare,
                      'profile_verified': not args.prepare, 'binary_redistribution_approved': False}))


if __name__ == '__main__':
    main()
