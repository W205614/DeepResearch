"""Build the fixed local Grafana profile, or verify its existing complete payload."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_build_20261110 as gate  # noqa: E402
from scripts.grafana_source_build_facts_20261110 import capture  # noqa: E402

CACHE = ROOT / '.cache/grafana-source-20261110'
PREFIX = 'deployment/vendors/grafana-20261110'
OLD_TOOLS = ('build_candidate_backends.py', 'capture_graphs.py', 'plugin-modules.json',
             'prepare_rebuild.py', 'verify_native_versions.py', 'verify_retained_assets.py')


def source_inputs(root=ROOT):
    """Bind actual recipe sources; the retained OCI has its separate immutable proof."""
    names = {gate.DOCKERFILE, gate.DOCKERFILE + '.dockerignore', 'deployment/docker/.gitattributes'}
    for directory in ('deployment/vendors/grafana/main', 'deployment/vendors/grafana-20261107/main', PREFIX):
        names.update(path.relative_to(root).as_posix() for path in (root / directory).rglob('*')
                     if path.is_file() and not set(path.relative_to(root / directory).parts) & {'inputs', 'out', '__pycache__'})
    names.update('deployment/vendors/grafana-20261107/' + name for name in OLD_TOOLS)
    names.update('deployment/vendors/grafana/' + name for name in
                 ('reviewed_backend_verifier.go', 'reviewed_backend_verifier_test.go'))
    return {name: gate.digest(gate.checked(root, name)) for name in sorted(names)}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def logged(command, output, filename):
    with (output / filename).open('wb') as stream:
        subprocess.run([str(value) for value in command], cwd=ROOT, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)


def reuse_exact_retained_input(output):
    from scripts import fetch_grafana_candidate as transport
    from scripts.fetch_grafana_fixed_20261107 import loader_profile

    old_profile = loader_profile()
    request = transport.authorized_request(ROOT, profile=old_profile)
    inspection = transport.inspect(gate.retained.APPROVED_TAG)
    binding = request['binding']
    if not transport.exact_image(inspection, binding):
        return False
    distribution = transport.load_transport(ROOT, binding, profile=old_profile)
    publication = transport.verify_publication(distribution, binding, profile=old_profile)
    write(output / 'retained-input.json', {'passed': True, 'input_only': True,
        'source': 'existing_exact_content_addressed_image', 'image_id': inspection['Id'],
        'portable_config_digest': binding['config_digest'], 'diff_ids': binding['diff_ids'],
        'publication': publication, 'request_sha256': old_profile.consumer.REQUEST_SHA256,
        'new_runtime_approved': False})
    return True


def verify_existing(output, profile):
    if source_inputs() != profile['source_inputs']:
        raise ValueError('Current build inputs differ; no old image fallback is allowed')
    receipt = gate.read(CACHE / 'build-receipt.json')
    inspection, = json.loads(subprocess.check_output(['docker', 'image', 'inspect', gate.IMAGE]))
    gate.verify_receipt(receipt, inspection, profile)
    actual = capture(inspection['Id'], output / 'actual-payload', save_config=True)
    if (actual['runtime_manifest_sha256'] != receipt['runtime_manifest_sha256']
            or actual['config_digest'] != receipt['config_digest']):
        raise ValueError('Existing image payload or portable config differs; no fallback or rebuild was performed')
    write(output / 'image-inspect.json', inspection)
    write(output / 'build-receipt.json', receipt)
    return receipt


def build(args):
    if args.prepare and args.no_build:
        raise ValueError('Preparation cannot authorize --no-build startup')
    profile = None if args.prepare else gate.load_profile()
    output = args.output_dir.resolve()
    if (not output.is_relative_to((ROOT / '.cache').resolve()) or output.exists()
            or output.is_symlink()):
        raise ValueError('Use a new build evidence directory under this checkout .cache')
    output.mkdir(parents=True, exist_ok=False)
    if args.no_build:
        return verify_existing(output, profile)
    sources = source_inputs()
    if profile is not None and sources != profile['source_inputs']:
        raise ValueError('Complete build inputs differ from the verified source profile')
    started = {'started_at_utc': datetime.now(timezone.utc).isoformat(), 'source_inputs': sources,
               'preparation_only': args.prepare, 'published': False, 'daily_environment_changed': False}
    write(output / 'build-start.json', started)
    # This is an input-only reuse of the independently checked older artifact.
    # Its approval does not authorize the new main or BusyBox runtime.
    if not reuse_exact_retained_input(output):
        loader_output = ROOT / '.cache/eval' / ('grafana-source-input-' + uuid.uuid4().hex[:12]) / 'retained-input.json'
        logged([sys.executable, '-B', ROOT / 'scripts/fetch_grafana_fixed_20261107.py',
                '--output', loader_output], output, 'retained-input.log')
        write(output / 'retained-input.json', gate.read(loader_output))
    source = args.source_repository or CACHE / 'upstream.git'
    logged([sys.executable, '-B', ROOT / PREFIX / 'prefetch_source.py', '--output', source,
            '--evidence', output / 'source-transport'], output, 'source-transport.log')
    empty = output / 'empty-module-transport'
    empty.mkdir()
    contexts = {'grafanasource': source, 'moduletransport': args.module_transport or empty,
                'thrifttransport': args.thrift_transport or empty,
                'busyboxtransport': args.busybox_transport or empty}
    command = ['docker', 'buildx', 'build', '--progress', 'plain', '--load', '--pull=false',
               '--metadata-file', str(output / 'build-metadata.json')]
    for name, path in contexts.items():
        if not path.is_dir() or path.is_symlink():
            raise ValueError('An explicit source transport context is unavailable')
        command += ['--build-context', name + '=' + str(path.resolve())]
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
    if args.prepare:
        revision += '+uncommitted-validation-inputs'
    command += ['--build-arg', 'RETAINED_ASSET_IMAGE=' + gate.retained.APPROVED_TAG,
                '--build-arg', 'PROJECT_SOURCE_REVISION=' + revision,
                '--build-arg', 'PROJECT_INPUTS_SHA256=' + gate.manifest_hash(sources),
                '--tag', gate.IMAGE, '--file', gate.DOCKERFILE, '.']
    write(output / 'build-command.json', command)
    logged(command, output, 'build.log')
    if source_inputs() != sources:
        raise ValueError('Source changed during the build; no verified receipt was issued')
    facts = capture(gate.IMAGE, output / 'actual-payload', save_config=True)
    files = {row['path']: row for row in facts['runtime_manifest']}
    baseline = gate.retained.load_request()['binding']['binary_sha256']
    native = {name: files[name]['sha256'] for name in baseline}
    if (native[gate.MAIN] == baseline[gate.MAIN]
            or any(native[name] != old for name, old in baseline.items() if name != gate.MAIN)):
        raise ValueError('The actual image must contain the new main and thirteen exact retained plugins')
    receipt = {'schema': 'grafana-source-build-receipt-v1', 'profile': gate.PROFILE,
               'image': gate.IMAGE, 'source_inputs': sources, 'native_sha256': native,
               'runtime_manifest_sha256': facts['runtime_manifest_sha256'],
               'runtime_config': gate.runtime_config(facts['inspection']['Config']),
               'image_id': facts['image_id'], 'diff_ids': facts['diff_ids'],
               'config_digest': facts['config_digest'], 'prepared_at_utc': datetime.now(timezone.utc).isoformat(),
               'preparation_only': args.prepare, 'binary_redistribution_approved': False}
    write(output / 'image-inspect.json', facts['inspection'])
    write(output / 'build-receipt.json', receipt)
    if profile is not None:
        gate.verify_receipt(receipt, facts['inspection'], profile)
        write(CACHE / 'build-receipt.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=CACHE / ('run-' + uuid.uuid4().hex[:12]))
    parser.add_argument('--no-build', action='store_true')
    parser.add_argument('--prepare', action='store_true', help='Build unapproved review facts only; cannot authorize startup')
    for name in ('source-repository', 'module-transport', 'thrift-transport', 'busybox-transport'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = build(args)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'error_type': type(error).__name__,
                  'error': str(error)[:240] if isinstance(error, ValueError) else 'Build failed; original logs retained',
                  'binary_redistribution_approved': False}
        print(json.dumps(result))
        return 1
    print(json.dumps({'passed': not args.prepare, 'operational_build': True, 'preparation_only': args.prepare,
                      'image_id': receipt['image_id'], 'binary_redistribution_approved': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
