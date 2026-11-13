"""Collect an unfiltered full native inventory; collection never approves the image."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_native_jvm import executable, run, validate_source  # noqa: E402
from scripts.go_native_database_transport_20261112 import prepare_database  # noqa: E402


def collect(args):
    output = args.output_dir.resolve()
    if not output.is_relative_to(ROOT / '.cache') or output.exists() or output.is_symlink():
        raise ValueError('Use a new collector directory below the workspace cache')
    output.mkdir(parents=True)
    result = {'operational_collection': False, 'security_gate_claim': False,
              'binary_redistribution_approved': False}
    try:
        receipt = json.loads(args.build_receipt.read_bytes())
        inspection, = json.loads(run(['docker', 'image', 'inspect', args.image]))
        if inspection['Id'] != receipt['image_id'] or inspection['RootFS']['Layers'] != receipt['diff_ids']:
            raise ValueError('Actual image identity differs from the build observation')
        lock = json.loads((ROOT / 'deployment/security/native-jvm-tools.json').read_bytes())
        tools = {name: executable(name, lock, args.tool_dir, args.tool_dir) for name in ('syft', 'grype')}
        env = {k: v for k, v in os.environ.items() if not k.startswith(('SYFT_', 'GRYPE_'))}
        env.update(GRYPE_CHECK_FOR_APP_UPDATE='false', SYFT_CHECK_FOR_APP_UPDATE='false')
        result['database'] = {}
        prepared = prepare_database(tools['grype'], lock['database'], args.db_cache, output,
                                    env, result['database'], archive_transport=args.database_archive)
        syconfig, grconfig = output / 'syft-config.json', output / 'grype-config.json'
        syconfig.write_bytes(b'{"exclude":[],"check-for-app-update":false}\n')
        grconfig.write_bytes(b'{"ignore":[],"only-fixed":false,"only-notfixed":false,"check-for-app-update":false}\n')
        sbom_path, report_path = output / 'full-syft.json', output / 'full-grype.json'
        run([tools['syft'], '--config', syconfig, 'scan', 'docker:' + inspection['Id'],
             '-o', 'syft-json=' + str(sbom_path), '--quiet'], env=prepared.env)
        sbom = json.loads(sbom_path.read_bytes())
        validate_source(sbom, inspection)
        config = base64.b64decode(sbom['source']['metadata']['config'], validate=True)
        if 'sha256:' + hashlib.sha256(config).hexdigest() != receipt['config_digest']:
            raise ValueError('Full SBOM portable config differs from the actual image')
        run([tools['grype'], '--config', grconfig, 'sbom:' + str(sbom_path),
             '-o', 'json', '--file', report_path, '--quiet'], env=prepared.env)
        prepared.assert_unchanged('after-complete-minio-native-scan')
        report = json.loads(report_path.read_bytes())
        descriptor = report['descriptor']
        if (report.get('ignoredMatches') or not isinstance(report['matches'], list)
                or descriptor['name'] != 'grype'
                or descriptor['version'] != lock['tools']['grype']['version'].removeprefix('v')
                or descriptor['db']['status']['valid'] is not True
                or report['source']['target']['imageID'] != receipt['config_digest']):
            raise ValueError('Full raw scan is filtered, failed or bound to another image')
        result.update(operational_collection=True, image_id=inspection['Id'],
                      config_digest=receipt['config_digest'], syft_exit_code=0, grype_exit_code=0,
                      sbom_sha256=hashlib.sha256(sbom_path.read_bytes()).hexdigest(),
                      report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
                      raw_counts=dict(Counter(row['vulnerability']['severity'] for row in report['matches'])),
                      package_types=dict(Counter(row['type'] for row in sbom['artifacts'])))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        result['error_type'] = type(error).__name__
        if isinstance(error, ValueError):
            result['error'] = str(error)[:240]
    (output / 'collector.json').write_bytes((json.dumps(result, indent=2) + '\n').encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--build-receipt', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--tool-dir', type=Path, default=ROOT / '.cache/native-jvm/tools')
    parser.add_argument('--db-cache', required=True, type=Path)
    parser.add_argument('--database-archive', type=Path)
    result = collect(parser.parse_args())
    print(json.dumps({k: v for k, v in result.items() if k != 'database'}))
    return 0 if result['operational_collection'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
