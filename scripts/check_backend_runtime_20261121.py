"""Check the actual Backend JRE, BusyBox and minimal Alpine runtime."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_backend_busybox_20261113 as busybox  # noqa: E402
from scripts import check_native_jvm as native  # noqa: E402
from scripts.go_native_database_transport_20261112 import prepare_database  # noqa: E402

REMOVED_PACKAGES = {'apk-tools', 'libapk', 'ssl_client', 'openssl', 'libcrypto3', 'libssl3', 'ca-certificates'}
REMOVED_FILES = {'apk', 'ssl_client', 'openssl', 'libcrypto.so.3', 'libssl.so.3'}


def verify_full_inventory(container, report, sbom, profile, output):
    native.validate_full_inventory(sbom, {**profile, 'minimum_packages': {
        **profile['minimum_packages'], 'apk': profile['minimum_packages']['apk'] - len(REMOVED_PACKAGES),
    }})
    packages = {row['name'] for row in sbom['artifacts'] if row['type'] == 'apk'}
    busybox.require(not packages & REMOVED_PACKAGES and 'ca-certificates-bundle' in packages,
                    'Backend Alpine runtime contains native TLS tools or lacks its CA bundle')
    for row in sbom['files']:
        path = Path(row['location']['path'])
        imports = (row.get('executable') or {}).get('importedLibraries', [])
        busybox.require(path.name not in REMOVED_FILES and not REMOVED_FILES.intersection(imports),
                        'Backend filesystem still contains or links a removed native TLS component')

    raw = (ROOT / busybox.PROFILE).read_bytes()
    busybox.require(busybox.sha(raw) == busybox.RETAINED_PROFILE_SHA256, 'Original BusyBox proof differs')
    baseline = json.loads(raw)
    for name in ('/bin/busybox', '/lib/ld-musl-x86_64.so.1'):
        target = output / Path(name).name
        native.run(['docker', 'cp', container + ':' + name, target])
        payload = target.read_bytes()
        digest = busybox.sha(payload)
        busybox.require({'bytes': len(payload), 'sha256': digest} == baseline['payload'][name],
                        'Reviewed Backend payload differs: ' + name)
        entries = [entry for entry in sbom['files'] if entry['location']['path'] == name]
        busybox.require(len(entries) == 1 and {'algorithm': 'sha256', 'value': digest} in entries[0]['digests'],
                        'Backend SBOM differs from the actual payload: ' + name)
        if name in baseline['needed_libraries']:
            busybox.require(entries[0]['executable']['importedLibraries'] == baseline['needed_libraries'][name],
                            'Backend BusyBox import list differs from the reviewed binary')
    shell = native.run(['docker', 'cp', container + ':/bin/sh', '-'])
    busybox.require(busybox.link_target(shell) == '/bin/busybox', 'Backend shell does not use the fixed BusyBox')
    archive = native.run(['docker', 'cp', container + ':' + busybox.LEGAL_ROOT + '/.', '-'])
    (output / 'legal-source.tar').write_bytes(archive)
    recipe = (ROOT / busybox.RECIPE).read_bytes()
    expected = {**baseline['legal_source_files'], busybox.LEGAL_RECIPE: {
        'bytes': len(recipe), 'sha256': busybox.sha(recipe),
    }}
    busybox.require(busybox.archive_files(archive) == expected, 'Backend BusyBox legal and source closure differs')

    findings = Counter((row['vulnerability']['id'], row['artifact']['name'], row['artifact']['version'],
                        row['artifact']['type'], row['vulnerability']['severity'], row['vulnerability']['namespace'])
                       for row in report['matches'])
    reviewed = Counter(tuple(row) for row in baseline['retained_finding_identities'] if row[1] != 'ssl_client')
    busybox.require(findings == reviewed, 'Backend full Grype findings differ from the two reviewed BusyBox rows')
    return {'raw_findings': sum(findings.values()), 'verified_fixed': sum(reviewed.values()), 'unresolved': 0}


def check(args):
    args.output_dir.mkdir(parents=True, exist_ok=False)
    lock = json.loads(native.LOCK.read_bytes())
    profile = lock['profiles']['business-java']
    manifest = ROOT / profile['file_manifest']
    busybox.require(native.digest(manifest) == profile['file_manifest_sha256'], 'Official JRE manifest differs')
    syft, grype = (native.executable(tool, lock, args.tool_dir, args.tool_dir) for tool in ('syft', 'grype'))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SYFT_', 'GRYPE_'))}
    database = {}
    prepared = prepare_database(grype, lock['database'], args.db_cache, args.output_dir, env, database,
                                archive_transport=args.database_archive)
    syft_config, grype_config = (args.output_dir / name for name in ('syft-config.json', 'grype-config.json'))
    syft_config.write_bytes(b'{"exclude":[],"check-for-app-update":false}\n')
    grype_config.write_bytes(b'{"ignore":[],"only-fixed":false,"only-notfixed":false,"check-for-app-update":false}\n')
    inspection, = json.loads(native.run(['docker', 'image', 'inspect', args.image]))
    image = inspection['Id']
    container = 'dr-backend-runtime-audit-' + uuid.uuid4().hex
    native.run(['docker', 'create', '--name', container, '--network', 'none', '--entrypoint', '/bin/true', image])
    result = {'actual_image_id': image, 'database': database, 'raw_report_modified': False}
    try:
        jre = '/opt/java/openjdk'
        with subprocess.Popen(['docker', 'cp', f'{container}:{jre}/.', '-'], stdout=subprocess.PIPE) as stream:
            result['actual_JRE_bytes'] = native.verify_jre_tar(stream.stdout, json.loads(manifest.read_bytes())['entries'])
            busybox.require(stream.wait(timeout=30) == 0, 'Backend JRE export failed')
        for mode in ('native', 'full'):
            sbom_path = args.output_dir / (mode + '-syft.json')
            report_path = args.output_dir / (mode + '-grype.json')
            scan = [syft, '--config', syft_config, 'scan', 'docker:' + image,
                    '-o', 'syft-json=' + str(sbom_path), '--quiet']
            if mode == 'native':
                scan += ['--override-default-catalogers', 'java-jvm-cataloger']
            native.run(scan, env=prepared.env)
            sbom = json.loads(sbom_path.read_bytes())
            native.validate_source(sbom, inspection)
            native.run([grype, '--config', grype_config, 'sbom:' + str(sbom_path), '-o', 'json',
                        '--file', report_path, '--quiet'], env=prepared.env)
            prepared.assert_unchanged('after-' + mode + '-scan')
            report = json.loads(report_path.read_bytes())
            result[mode] = {'raw_counts': native.validate_scan(report, sbom), 'report_sha256': native.digest(report_path)}
            if mode == 'native':
                result['JVM'] = native.validate_jvm(sbom, profile, lock['expected_jvm'])
            else:
                result['runtime'] = verify_full_inventory(container, report, sbom, profile, args.output_dir)
    finally:
        native.run(['docker', 'rm', container])
    return {**result, 'passed': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--tool-dir', type=Path, default=ROOT / '.cache/native-jvm/tools')
    parser.add_argument('--db-cache', type=Path, default=ROOT / '.cache/native-jvm/grype-db')
    parser.add_argument('--database-archive', type=Path)
    args = parser.parse_args()
    try:
        result = check(args)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'error_type': type(error).__name__}
        if isinstance(error, ValueError):
            result['error'] = str(error)
    (args.output_dir / 'summary.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'database'}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
