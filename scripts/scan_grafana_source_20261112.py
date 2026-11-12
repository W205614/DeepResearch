"""Collect complete raw native reports for the separate Go 1.27.4 source subject."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_build_20261112 as gate  # noqa: E402
from scripts.check_native_jvm import executable, run, validate_source  # noqa: E402
from scripts.go_native_database_transport_20261112 import prepare_database  # noqa: E402
from scripts.scan_grafana_source_20261110 import verify_actual_native  # noqa: E402

def collect(args, *, root=ROOT, consumer=gate, prepare_profile=gate.preparation_profile):
    result = {'operational_collection': False, 'verified': False, 'security_gate_claim': False,
              'binary_redistribution_approved': False, 'preparation_only': args.prepare}
    output_created = False
    try:
        profile = None if args.prepare else consumer.load_profile(root)
        receipt = consumer.read(args.build_receipt)
        if args.prepare:
            profile = prepare_profile(receipt, root)
        cache = (root / '.cache').resolve()
        if (not cache.is_relative_to(root.resolve()) or not args.output_dir.resolve().is_relative_to(cache)
                or args.output_dir.exists() or args.output_dir.is_symlink()):
            raise ValueError('Use a new collector directory under the workspace .cache')
        args.output_dir.mkdir(parents=True, exist_ok=False)
        output_created = True
        inspection, = json.loads(run(['docker', 'image', 'inspect', args.image]))
        consumer.verify_receipt(receipt, inspection, profile, root=root)
        result['sourceBuild'] = {'profile': consumer.PROFILE, 'actualImageID': inspection['Id'],
            'portableConfigDigest': receipt['config_digest'], 'diffIDs': receipt['diff_ids'],
            'buildReceiptSha256': consumer.digest(args.build_receipt),
            'sourceInputsSha256': consumer.manifest_hash(receipt['source_inputs']),
            'profile_review_verified': not args.prepare}
        (args.output_dir / 'image-inspect.json').write_text(json.dumps(inspection, indent=2) + '\n', encoding='utf-8')
        result['native_audit'] = {}
        verify_actual_native(inspection['Id'], receipt['native_sha256'], result['native_audit'])
        lock = consumer.read(root / 'deployment/security/native-jvm-tools.json')
        tools = {name: executable(name, lock, args.tool_dir, args.tool_dir) for name in ('syft', 'grype')}
        env = {key: value for key, value in os.environ.items() if not key.startswith(('GRYPE_', 'SYFT_'))}
        env.update(GRYPE_CHECK_FOR_APP_UPDATE='false', SYFT_CHECK_FOR_APP_UPDATE='false')
        result['tools'] = {}
        for name, path in tools.items():
            actual_version = json.loads(run([path, 'version', '-o', 'json'], env=env))['version']
            if actual_version != lock['tools'][name]['version'].removeprefix('v'):
                raise ValueError('Pinned native scanner version differs: ' + name)
            result['tools'][name] = {'version': actual_version, 'sha256': consumer.digest(path)}
        result['database'] = {}
        prepared = prepare_database(tools['grype'], lock['database'], args.db_cache,
                                    args.output_dir, env, result['database'], archive_transport=args.database_archive)
        syconfig, grconfig = args.output_dir / 'syft-config.json', args.output_dir / 'grype-config.json'
        syconfig.write_text(json.dumps({'exclude': [], 'check-for-app-update': False}) + '\n', encoding='utf-8')
        grconfig.write_text(json.dumps({'ignore': [], 'only-fixed': False, 'only-notfixed': False,
                                        'check-for-app-update': False}) + '\n', encoding='utf-8')
        sbom_path, report_path = args.output_dir / 'full-syft.json', args.output_dir / 'full-grype.json'
        run([tools['syft'], '--config', syconfig, 'scan', 'docker:' + inspection['Id'],
             '-o', 'syft-json=' + str(sbom_path), '--quiet'], env=prepared.env)
        sbom = consumer.read(sbom_path)
        validate_source(sbom, inspection)
        config_raw = base64.b64decode(sbom['source']['metadata']['config'], validate=True)
        if ('sha256:' + hashlib.sha256(config_raw).hexdigest() != receipt['config_digest']
                or consumer.runtime_config(json.loads(config_raw).get('config', {})) != receipt['runtime_config']
                or not isinstance(sbom.get('artifacts'), list) or not sbom['artifacts']):
            raise ValueError('Complete native inventory differs from the actual build receipt')
        run([tools['grype'], '--config', grconfig, 'sbom:' + str(sbom_path),
             '-o', 'json', '--file', report_path, '--quiet'], env=prepared.env)
        prepared.assert_unchanged('after-full-source-build-scan')
        report = consumer.read(report_path)
        if (not isinstance(report.get('matches'), list) or report.get('ignoredMatches')
                or report.get('descriptor', {}).get('name') != 'grype'
                or report.get('descriptor', {}).get('version') != lock['tools']['grype']['version'].removeprefix('v')
                or report.get('descriptor', {}).get('db', {}).get('status', {}).get('valid') is not True
                or report.get('source', {}).get('target', {}).get('imageID') != receipt['config_digest']):
            raise ValueError('Complete native scan/database/source evidence is unavailable or filtered')
        result.update(operational_collection=True, sbom_sha256=consumer.digest(sbom_path),
            raw_grype_sha256=consumer.digest(report_path), syft_types=dict(Counter(a['type'] for a in sbom['artifacts'])),
            raw_grype_counts=dict(Counter(row['vulnerability']['severity'] for row in report['matches'])),
            required_next_step='check_grafana_source_build_20261112.py with complete raw Trivy and this native collection')
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        result['error_type'] = type(error).__name__
        if isinstance(error, ValueError):
            result['error'] = str(error)[:240]
    if output_created:
        (args.output_dir / 'collector.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--tool-dir', type=Path, default=ROOT / '.cache/native-jvm/tools')
    parser.add_argument('--db-cache', type=Path, default=ROOT / '.cache/native-jvm/grype-db')
    parser.add_argument('--database-archive', type=Path,
                        help='Optional complete archive; current publisher SHA/age and fresh import remain mandatory')
    parser.add_argument('--prepare', action='store_true', help='Collect unapproved facts only; never grants deployment or publication')
    result = collect(parser.parse_args(argv))
    print(json.dumps({name: result[name] for name in ('operational_collection', 'verified', 'preparation_only',
        'sourceBuild', 'error_type', 'error') if name in result}))
    return 0 if result['operational_collection'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
