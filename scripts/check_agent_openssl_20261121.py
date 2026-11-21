"""Verify the upstream-fixed OpenSSL runtime while preserving the complete Debian scan."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_agent_native_packages import digest, require  # noqa: E402

REVISION = 'f3acb0a3328046741479482c51cec815839e1b05'
FINDING = {'id': 'CVE-2026-124709', 'package': 'libssl3t64', 'severity': 'HIGH'}
DEBIAN_VERSION = '3.5.7-1~deb13u3'
LIBRARIES = {
    'usr/lib/x86_64-linux-gnu/libcrypto.so.3': '6a94ca2354f32b1164713566608161eaa60e185bf4de2bc0210af721cb656953',
    'usr/lib/x86_64-linux-gnu/libssl.so.3': '22fbb52bacfd82079eaa8fe3dd586ebdbc97e816e8dc62a931237d3a29c712e3',
}
RUNTIME_PROBE = '''import hashlib, json, pathlib, ssl
ssl.create_default_context()
root = pathlib.Path('/usr/share/deepresearch')
manifest = dict(reversed(line.split()) for line in (root / 'openssl-libraries.sha256').read_text().splitlines())
names = {pathlib.Path(name).name for name in manifest}
loaded = {line.split()[-1] for line in pathlib.Path('/proc/self/maps').read_text().splitlines()
          if pathlib.Path(line.split()[-1]).name in names}
actual = {p.lstrip('/'): hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in sorted(loaded)}
print(json.dumps({'revision': (root / 'openssl-revision').read_text().strip(),
                  'version': ssl.OPENSSL_VERSION, 'manifest': manifest, 'loaded': actual}))
'''


def assess(args):
    args.output_dir.mkdir(parents=True, exist_ok=False)
    legacy_dir = args.output_dir / 'debian'
    prior = subprocess.run([sys.executable, str(ROOT / 'scripts/check_agent_native_packages.py'),
                            '--image', args.image, '--scan', str(args.scan),
                            '--scanner-exit-code', str(args.scanner_exit_code),
                            '--output-dir', str(legacy_dir)], check=False)
    verdict = json.loads((legacy_dir / 'verdict.json').read_bytes())
    require(verdict.get('raw_scan_sha256') == digest(args.scan) and verdict.get('raw_reports_modified') is False
            and prior.returncode in {0, 1} and verdict['passed'] == (prior.returncode == 0)
            and (verdict['unresolved'] == [] if verdict['passed'] else verdict['unresolved'] == [FINDING]),
            'The original Agent gate failed outside the exact OpenSSL finding')
    image = verdict['image_id']
    probe = json.loads(subprocess.check_output(
        ['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', image, '-c', RUNTIME_PROBE],
        timeout=120))
    require(probe['revision'] == REVISION and probe['version'].startswith('OpenSSL 3.5.10-dev ')
            and probe['loaded'] == probe['manifest'] == LIBRARIES,
            'The immutable runtime does not load both libraries built from the upstream fix')
    if not verdict['passed']:
        report = json.loads(args.scan.read_bytes())
        rows = [row for result in report['Results'] if (result['Class'], result['Type']) == ('os-pkgs', 'debian')
                for row in result.get('Vulnerabilities', []) if row['VulnerabilityID'] == FINDING['id']]
        require(len(rows) == 1 and rows[0]['PkgName'] == FINDING['package']
                and rows[0]['InstalledVersion'] == DEBIAN_VERSION and rows[0]['Severity'] == FINDING['severity']
                and rows[0].get('DataSource', {}).get('ID') == 'debian', 'The exact Debian OpenSSL tuple changed')
        verdict['verified_fixed'].append({**FINDING, 'version': DEBIAN_VERSION, 'upstream_fix': REVISION,
            'disposition': 'FIXED_IN_LOADED_LIBRARIES_BUILT_FROM_UPSTREAM_SOURCE'})
    return {**verdict, 'passed': True, 'unresolved': [], 'openssl_runtime': probe}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--scan', required=True, type=Path)
    parser.add_argument('--scanner-exit-code', required=True, type=int)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = assess(args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'error_type': type(error).__name__,
                  'error': str(error) if isinstance(error, ValueError) else 'Required Agent evidence unavailable'}
    (args.output_dir / 'verdict.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
