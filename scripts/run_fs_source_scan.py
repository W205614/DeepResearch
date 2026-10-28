"""Produce fresh complete source-scan evidence; completion is not a security verdict.

Run on the final clean checkout before check_fs_source_applicability.py. Raw JSON
and logs remain intact. A finding exit code of zero distinguishes scanner errors
from vulnerability findings; only the separate checker may decide applicability.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SCOPE = 'deployment/security/grafana-source2-technical-scope.json'
SEVERITIES = ['UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
MANIFESTS = {'go.mod': 'gomod', 'pom.xml': 'pom', 'package-lock.json': 'npm', 'uv.lock': 'uv'}
MAX_REPORT_BYTES = 128 * 1024**2
EMPTY_SHA256 = hashlib.sha256(b'').hexdigest()


class CompletionError(ValueError):
    pass


def require(value, message):
    if not value:
        raise CompletionError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_neutral_ignorefile(path):
    require(path.is_file() and not path.is_symlink() and path.read_bytes() == b''
            and file_hash(path) == EMPTY_SHA256, 'Neutral ignore file missing, linked or contains rules')


def snapshot(root):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE)
    head = git('rev-parse', 'HEAD').decode().strip()
    require(bool(re.fullmatch('[0-9a-f]{40}', head)), 'Invalid source commit')
    require(not git('status', '--porcelain', '--untracked-files=no').strip(), 'Tracked source is dirty')
    tracked = []
    for entry in git('ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        metadata, name = entry.split(b'\t', 1)
        mode, oid, stage = metadata.decode().split()
        name = name.decode('utf-8')
        require(mode in {'100644', '100755'} and stage == '0', 'Nonregular/submodule/unmerged source needs explicit coverage')
        require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts,
                'Invalid tracked path')
        tracked.append((name, oid))
    return head, sorted(tracked)


def export_source(root, destination, tracked):
    archive_path = destination.parent / 'source.git.tar'
    subprocess.run(['git', 'archive', '--format=tar', '--output', str(archive_path), 'HEAD'],
                   cwd=root, capture_output=True, check=True)
    destination.mkdir()
    expected, seen, total = dict(tracked), set(), 0
    with tarfile.open(archive_path) as archive:
        for member in archive:
            name = member.name.rstrip('/')
            parts = PurePosixPath(name)
            require(not parts.is_absolute() and '..' not in parts.parts and '\\' not in name,
                    'Invalid archive member path')
            if member.isdir():
                continue
            require(member.isfile() and name in expected and name not in seen,
                    'Exported source differs from complete regular tracked tree')
            total += member.size
            require(0 <= member.size <= 256 * 1024**2 and total <= 1024**3, 'Source export exceeds bound')
            data = archive.extractfile(member).read()
            git_hash = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            require(git_hash == expected[name], 'Git archive transformed source bytes')
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(member.mode & 0o777)
            seen.add(name)
    require(seen == set(expected), 'Git export omitted tracked files')


def validate_report(scan, *, raw_sha, process_exit, started, finished, scope, expected_targets, source_commit):
    require(type(process_exit) is int and process_exit == 0, 'Scanner operational failure; exit1 is not a finding exit')
    require(isinstance(scan, dict) and scan.get('SchemaVersion') == 2 and scan.get('ArtifactType') == 'filesystem'
            and scan.get('Trivy', {}).get('Version') == scope['scanner_version'], 'Full scanner report metadata differs')
    for key in ('Errors', 'ModifiedFindings', 'SuppressedFindings'):
        require(key not in scan or (isinstance(scan[key], list) and not scan[key]), 'Raw report has failures/suppressions')
    report_id = scan.get('ReportID')
    require(isinstance(report_id, str) and bool(report_id.strip()), 'Fresh ReportID is missing')
    created_text = scan.get('CreatedAt')
    require(isinstance(created_text, str), 'Report creation time is missing')
    created = datetime.fromisoformat(created_text)
    require(created.tzinfo is not None and started <= created <= finished
            and 0 <= (finished - started).total_seconds() <= 7200, 'Report is old or outside actual scanner interval')
    results = scan.get('Results')
    require(isinstance(results, list), 'Full result list is missing')
    targets, language, findings = [], [], 0
    vulnerability_counts, secret_counts = Counter({s: 0 for s in SEVERITIES}), Counter({s: 0 for s in SEVERITIES})
    for result in results:
        require(isinstance(result, dict) and isinstance(result.get('Target'), str)
                and all(isinstance(result.get(k, ''), str) for k in ('Class', 'Type')), 'Invalid result identity')
        for key in ('Errors', 'ModifiedFindings', 'SuppressedFindings'):
            require(key not in result or (isinstance(result[key], list) and not result[key]), 'Result has failures/suppressions')
        target = [result['Target'], result.get('Class', ''), result.get('Type', '')]
        targets.append(target)
        if result.get('Class') == 'lang-pkgs':
            require(isinstance(result.get('Packages'), list) and bool(result['Packages']), 'Full package inventory is missing')
            language.append(target)
        for key in ('Vulnerabilities', 'Secrets'):
            values = result.get(key, [])
            require(isinstance(values, list), 'Invalid raw finding collection')
            for value in values:
                require(isinstance(value, dict) and value.get('Severity') in SEVERITIES, 'Invalid/filtered finding severity')
                (secret_counts if key == 'Secrets' else vulnerability_counts)[value['Severity']] += 1
            findings += len(values)
    require(len(set(map(tuple, targets))) == len(targets), 'Duplicate result targets')
    require(sorted(language) == sorted(expected_targets) and len(language) == len(expected_targets),
            'Complete tracked language-target coverage is missing or changed')
    return {'schema': 'fs-scan-completion-v1', 'completed': True, 'operational_success': True,
            'timed_out': False, 'scanner_version': scope['scanner_version'], 'raw_sha256': raw_sha,
            'raw_report_id': report_id, 'source_commit': source_commit, 'tracked_clean': True,
            'scanners': ['vuln', 'secret'], 'severities': list(scope['required_severities']),
            'configured_finding_exit_code': 0, 'process_exit_code': process_exit,
            'started_at': started.isoformat(), 'finished_at': finished.isoformat(), 'report_created_at': created_text,
            'filters': {'ignorefile_used': True, 'ignorefile_rules_empty': True,
                        'ignorefile_sha256': EMPTY_SHA256, 'ignorefile_mode': 'verified_empty_neutral',
                        'include_dev_dependencies': True,
                        'skip_files': [], 'skip_dirs': [],
                        'ignore_unfixed': False, 'vex_documents': []},
            'result_targets_sha256': hashlib.sha256(canonical(sorted(targets))).hexdigest(),
            'language_target_count': len(language), 'result_count': len(targets), 'raw_finding_count': findings,
            'raw_vulnerability_counts': dict(vulnerability_counts), 'raw_secret_counts': dict(secret_counts),
            'security_verdict': 'not_evaluated_completion_only'}


def run(root, scanner, output, *, severities=None, source_sha=None):
    root, scanner, output = root.resolve(), scanner.resolve(), output.resolve()
    require(not output.exists() and not output.is_symlink(), 'Use a fresh evidence directory; old reports are never reused')
    output.mkdir(parents=True)
    receipt = {'schema': 'fs-scan-completion-v1', 'completed': False, 'operational_success': False,
               'timed_out': False, 'security_verdict': 'not_evaluated_completion_only'}
    try:
        head, tracked = snapshot(root)
        expected_sha = source_sha or os.environ.get('GITHUB_SHA') or head
        require(head == expected_sha and (not os.environ.get('GITHUB_SHA') or head == os.environ['GITHUB_SHA']),
                'Checkout differs from expected final source SHA')
        require(SCOPE in dict(tracked), 'Coverage scope must belong to the committed source tree')
        scope = json.loads((root / SCOPE).read_bytes())['scope']
        required = scope['required_severities']
        require(isinstance(required, list) and len(required) == 5 and sorted(required) == sorted(SEVERITIES),
                'Source scanner raw severities differ')
        require(severities is None or severities == required, 'CLI severity coverage must exactly equal pending scope')
        require(scope['scanner_version'] == '0.75.0', 'Scanner version differs from fixed coverage')
        expected = sorted([[name, 'lang-pkgs', MANIFESTS[Path(name).name]]
                           for name, _ in tracked if Path(name).name in MANIFESTS])
        require(expected and len({tuple(target) for target in expected}) == len(expected)
                and all(target in expected for target in scope['required_scan_targets']),
                'Previously reviewed source manifest coverage is missing or ambiguous')
        source = output / 'source'
        export_source(root, source, tracked)
        # Explicit neutral config and a zero-byte ignore file prevent repository,
        # user-config or inherited TRIVY_* options from suppressing source rows.
        # Trivy0.75 rejects a missing explicitly named ignore file. No ignore
        # rules are used; the neutral file itself is recorded and byte-checked.
        config, ignorefile = output / 'empty-trivy.yaml', output / 'empty.ignore'
        config.write_bytes(b'{}\n')
        ignorefile.write_bytes(b'')
        verify_neutral_ignorefile(ignorefile)
        raw = output / 'raw.json'
        args = [str(scanner), 'fs', '--config', str(config), '--ignorefile', str(ignorefile),
                '--scanners', 'vuln,secret', '--severity', ','.join(required), '--list-all-pkgs', '--include-dev-deps',
                '--ignore-unfixed=false', '--format', 'json', '--output', str(raw), '--exit-code', '0',
                '--timeout', '5m', str(source)]
        environment = {k: v for k, v in os.environ.items() if not k.startswith('TRIVY_')}
        version = subprocess.run([str(scanner), '--version'], capture_output=True, timeout=30, env=environment, check=True)
        (output / 'scanner-version.log').write_bytes(version.stdout)
        require(re.search(rb'^Version:\s*0\.75\.0\s*$', version.stdout, re.MULTILINE), 'Actual scanner version differs')
        started = datetime.now(timezone.utc)
        receipt.update(source_commit=head, started_at=started.isoformat(), command=args,
                       scanner_sha256=file_hash(scanner), tracked_tree_sha256=hashlib.sha256(canonical(tracked)).hexdigest())
        try:
            process = subprocess.run(args, cwd=output, capture_output=True, timeout=330, env=environment)
        except subprocess.TimeoutExpired as error:
            (output / 'stdout.private.log').write_bytes(error.stdout or b'')
            (output / 'stderr.private.log').write_bytes(error.stderr or b'')
            receipt['timed_out'] = True
            raise CompletionError('Scanner timed out') from None
        finished = datetime.now(timezone.utc)
        (output / 'stdout.private.log').write_bytes(process.stdout)
        (output / 'stderr.private.log').write_bytes(process.stderr)
        receipt.update(process_exit_code=process.returncode, finished_at=finished.isoformat())
        require(process.returncode == 0, 'Scanner operational failure; finding exit is configured zero')
        require(raw.is_file() and not raw.is_symlink() and 0 < raw.stat().st_size <= MAX_REPORT_BYTES,
                'Fresh raw report is missing or oversized')
        verify_neutral_ignorefile(ignorefile)
        require(not config.is_symlink() and config.read_bytes() == b'{}\n', 'Neutral scanner config changed')
        require(snapshot(root) == (head, tracked), 'Tracked source changed during scan')
        checked = validate_report(json.loads(raw.read_bytes()), raw_sha=file_hash(raw), process_exit=process.returncode,
                                  started=started, finished=finished, scope=scope,
                                  expected_targets=expected, source_commit=head)
        receipt.update(checked, scan_root='fresh_full_tracked_checkout',
                       scope_sha256=hashlib.sha256(canonical(scope)).hexdigest(),
                       neutral_ignorefile={'bytes': 0, 'sha256': file_hash(ignorefile), 'contains_ignore_rules': False},
                       github_run_id=os.environ.get('GITHUB_RUN_ID'), github_run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'))
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        receipt.update(error_type=type(error).__name__, error=str(error) if isinstance(error, CompletionError)
                       else 'Source scanner or completion evidence failed; raw logs retained privately')
    (output / 'completion.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scanner', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--source-sha')
    parser.add_argument('--severities', help='If supplied, must exactly match pending scope order and values')
    args = parser.parse_args(argv)
    try:
        result = run(ROOT, args.scanner, args.output_dir, source_sha=args.source_sha,
                     severities=args.severities.split(',') if args.severities is not None else None)
    except (ValueError, OSError):
        result = {'completed': False, 'error': 'Fresh evidence directory required; existing evidence preserved'}
    print(json.dumps({k: result[k] for k in ('completed', 'operational_success', 'error') if k in result}))
    return 0 if result['completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
