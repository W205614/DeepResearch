"""Bounded exact-image runtime vulnerability inventory; never starts services.

Presence in a vulnerability database is not a demonstrated exploit. Unknown,
missing and timed-out scans remain explicit failures, never zero findings.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def capture(args, timeout=30):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError('Inventory command failed; output withheld')
    return result.stdout


def run(args):
    destination = Path(args.output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    command = ['docker', 'compose', '--env-file', args.env_file]
    for filename in args.compose_file:
        command += ['-f', filename]
    config = json.loads(capture([*command, '-p', args.project, '--profile', '*', 'config', '--format', 'json']))
    inventory, missing = {}, []
    for service, definition in config['services'].items():
        build = definition.get('build')
        custom_minio = bool(build and build.get('dockerfile') == 'Dockerfile.minio')
        if args.vendors_only and build and not custom_minio:
            continue
        reference = definition.get('image') or args.project + '-' + service
        try:
            image = json.loads(capture(['docker', 'image', 'inspect', reference]))[0]
        except Exception:
            missing.append({'service': service, 'reference': reference, 'state': 'not_present_not_scanned'})
            continue
        record = inventory.setdefault(image['Id'], {'image_id': image['Id'], 'references': [], 'repo_digests': image.get('RepoDigests', []), 'services': []})
        record['references'].append(reference)
        ports = definition.get('ports', [])
        record['services'].append({'service': service, 'published': [
            {'ip': item.get('host_ip', ''), 'port': item.get('published', ''), 'target': item.get('target', '')} for item in ports],
            'exposure': 'internal_only' if not ports else 'loopback' if all(item.get('host_ip') in {'127.0.0.1', '::1'} for item in ports) else 'public_or_unspecified'})
    for index, record in enumerate(inventory.values()):
        raw = destination / f'image-{index:02d}.json'
        started = time.monotonic()
        command = [args.trivy, 'image', '--cache-dir', args.cache_dir, '--scanners', 'vuln', '--severity', 'HIGH,CRITICAL',
                   '--timeout', f'{args.timeout}s', '--format', 'json', '--output', str(raw)]
        if args.cached_database:
            command += ['--skip-db-update', '--skip-java-db-update']
        command.append(record['image_id'])
        try:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=args.timeout + 10, check=False)
            diagnostic = destination / f'image-{index:02d}.scanner.log'
            diagnostic.write_bytes(result.stderr[-32768:])
            record['scanner_log'] = diagnostic.name
            record['scanner_return_code'] = result.returncode
            if result.returncode or not raw.is_file():
                raise RuntimeError('scanner_failed')
            report = json.loads(raw.read_text(encoding='utf-8'))
            findings = [v for item in report.get('Results', []) for v in item.get('Vulnerabilities', [])]
            record.update(state='scanned', finding_counts=dict(Counter(v['Severity'] for v in findings)),
                          findings=[{key: value.get(key, '') for key in ('VulnerabilityID', 'PkgName', 'InstalledVersion', 'FixedVersion', 'Severity', 'PrimaryURL')} for value in findings],
                          raw_report=raw.name, raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
        except subprocess.TimeoutExpired:
            record.update(state='timeout', findings=None)
        except Exception:
            record.update(state='scan_failed', findings=None)
        record['duration_seconds'] = round(time.monotonic() - started, 3)
        print(json.dumps({'image': index, 'services': [item['service'] for item in record['services']], 'state': record['state'], 'finding_counts': record.get('finding_counts')}), flush=True)
    summary = {'project': args.project, 'scope': 'existing resolved runtime image IDs; no pull/start/rebuild',
               'cached_database': args.cached_database, 'per_image_timeout_seconds': args.timeout,
               'exploitability': 'not established; package presence and service exposure require advisory-specific analysis',
               'images': list(inventory.values()), 'missing': missing,
               'passed': not missing and all(value['state'] == 'scanned' and not value['findings'] for value in inventory.values())}
    (destination / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--compose-file', action='append', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--trivy', required=True)
    parser.add_argument('--cache-dir', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--cached-database', action='store_true')
    parser.add_argument('--vendors-only', action='store_true')
    args = parser.parse_args()
    if not 15 <= args.timeout <= 180:
        parser.error('Per-image timeout must stay between 15 and 180 seconds')
    raise SystemExit(0 if run(args)['passed'] else 1)
