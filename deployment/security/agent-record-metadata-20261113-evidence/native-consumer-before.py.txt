"""Verify exact fixed Debian library bytes before adjudicating reviewed findings.

Full raw Trivy reports are preserved. This is a fixed-code check, not risk
acceptance: every other HIGH/CRITICAL and every secret still blocks.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'deployment/security/agent-native-packages.json'
LOCK_SHA256 = 'c8354130c02ef81c173bcc62cfb2af717ec7af8f5acf98b2fb3a2782d0260454'
SUPPLEMENT_PATH = 'deployment/security/agent-native-fixed-code-supplement-20261107.json'
SUPPLEMENT_SHA256 = 'f0960cbc117072656b5038706634e11f79281fe876f84642fa234953f25338df'
SUPPLEMENT_FIXES = {
    'CVE-2026-119374': 'd9de6eedbe918c074c7556400fa21df5960704d9',
    'CVE-2026-119379': '64459c87cc702d600704fb77ee40ca1782398a52',
}
LEVELS = {'UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'}
RUNTIME_PROBE = '''import ctypes,hashlib,json,pathlib,sqlite3,zlib
db=sqlite3.connect(':memory:')
# SQLite upstream e1c9934: negative docids in detail=none prefix-list merging.
db.executescript("""CREATE VIRTUAL TABLE t1 USING fts5(x, detail=none);
WITH RECURSIVE c(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM c WHERE x<55)
INSERT INTO t1(rowid,x) SELECT x,'ab' FROM c;
INSERT INTO t1(rowid,x) VALUES(-9223372036854775808,'abc');""")
assert db.execute("SELECT count(*) FROM t1('a*')").fetchone()==(56,)
db.close()
assert zlib.decompress(zlib.compress(b'native probe')) == b'native probe'
lib=ctypes.CDLL('libz.so.1')
lib.gzopen.argtypes=[ctypes.c_char_p,ctypes.c_char_p];lib.gzopen.restype=ctypes.c_void_p
lib.gzwrite.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint];lib.gzwrite.restype=ctypes.c_int
lib.gzclearerr.argtypes=[ctypes.c_void_p];lib.gzclose.argtypes=[ctypes.c_void_p]
f=lib.gzopen(b'/dev/full',b'wbT');assert f
failures=0
for i in range(100):
    data=ctypes.create_string_buffer(b'x'*(32768+i))
    failures+=int(lib.gzwrite(f,data,len(data)-1)==0)
    lib.gzclearerr(f)
lib.gzclose(f);assert failures==100
paths={line.split()[-1] for line in pathlib.Path('/proc/self/maps').read_text().splitlines()}
libraries={name:[{'path':p,'sha256':hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()}
                 for p in paths if pathlib.Path(p).name.startswith(prefix)]
           for name,prefix in [('libsqlite3-0','libsqlite3.so.'),('zlib1g','libz.so.')]}
print(json.dumps({'sqlite':sqlite3.sqlite_version,'zlib':zlib.ZLIB_RUNTIME_VERSION,'loaded':libraries,
                  'sqlite_upstream_negative_docid_prefix_count':56,'zlib_failed_write_checks':failures}))
'''


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_lock():
    require(digest(LOCK) == LOCK_SHA256, 'Reviewed native package lock changed')
    lock = json.loads(LOCK.read_bytes())
    for relative, info in lock['evidence'].items():
        require(digest(ROOT / relative) == info['sha256'], 'Native fixed-source evidence changed')
    return lock


def validate_supplement(review, lock):
    require(review.get('schema') == 'agent-native-fixed-code-supplement-v1'
            and review.get('review_kind') == 'FIXED_CODE' and review.get('risk_acceptance') is False
            and review.get('base_native_lock_sha256') == LOCK_SHA256,
            'Supplement must bind the unchanged native lock as fixed-code evidence')
    packages = [p for p in lock['packages'] if p['name'] == 'libsqlite3-0']
    require(len(packages) == 1, 'Exact original SQLite package proof required')
    package = packages[0]
    require(review.get('package') == {key: package[key] for key in (
        'name', 'version', 'source', 'archive_sha256', 'library', 'soname', 'library_sha256')}
        | {'architecture': 'amd64'}, 'Supplement cannot change the reviewed official package or ELF')
    expected = [{'id': advisory, 'package': package['name'], 'version': package['version'],
                 'severity': 'HIGH', 'class': 'os-pkgs', 'type': 'debian', 'data_source': 'debian',
                 'upstream_fix': commit, 'fixed_from_upstream': '3.54.0', 'debian_fixed_source': '3.54.1-1'}
                for advisory, commit in SUPPLEMENT_FIXES.items()]
    require(review.get('findings') == expected, 'Supplement must cover only the two exact reviewed HIGH tuples')
    return package


def load_supplement(lock, *, root=ROOT):
    path = root / SUPPLEMENT_PATH
    require(digest(path) == SUPPLEMENT_SHA256, 'Reviewed native fixed-code supplement changed')
    review = json.loads(path.read_bytes())
    validate_supplement(review, lock)
    require(isinstance(review.get('evidence'), dict) and len(review['evidence']) == 8,
            'Complete supplemental primary-source evidence required')
    for relative, info in review['evidence'].items():
        evidence = (root / relative).resolve()
        require(evidence.is_relative_to(root.resolve()) and digest(evidence) == info['sha256'],
                'Supplemental fixed-source evidence changed')
    return review


def command(args):
    return subprocess.check_output(args, stderr=subprocess.PIPE, timeout=120)


def collect(image, output, lock):
    inspection = json.loads(command(['docker', 'image', 'inspect', image]))[0]
    name = 'dr-agent-native-facts-' + uuid.uuid4().hex[:12]
    command(['docker', 'create', '--name', name, '--network', 'none', '--entrypoint', 'python',
             inspection['Id'], '-c', RUNTIME_PROBE])
    facts = {'image_id': inspection['Id'], 'diff_ids': inspection['RootFS']['Layers'],
             'audit_container_retained': name, 'packages': {}}
    for package in lock['packages']:
        directory = output / package['name']
        directory.mkdir()
        actual = {}
        for key, source in (('library', package['library']),
                            ('status', '/var/lib/dpkg/status.d/' + package['name'])):
            path = directory / key
            command(['docker', 'cp', name + ':' + source, str(path)])
            actual[key + '_sha256'] = digest(path)
            if key == 'status':
                actual['status'] = path.read_text(encoding='utf-8')
        require(actual['library_sha256'] == package['library_sha256'],
                'Actual library bytes differ from the fixed official package')
        status = dict(line.split(': ', 1) for line in actual['status'].splitlines()
                      if ': ' in line and not line.startswith(' '))
        require(status.get('Package') == package['name'] and status.get('Version') == package['version']
                and status.get('Architecture') == 'amd64' and status.get('Status') == 'install ok installed'
                and status.get('Source') == package['source'], 'Actual dpkg identity differs')
        facts['packages'][package['name']] = actual
    # Probe the actual loaded libraries too: a fixed file in /usr/lib cannot mask
    # an old copy selected through LD_LIBRARY_PATH or the interpreter's RUNPATH.
    try:
        probe = json.loads(command(['docker', 'start', '-a', name]))
    finally:
        state = json.loads(command(['docker', 'inspect', name]))[0]['State']
        if state['Running']:
            command(['docker', 'stop', '--time', '5', name])
    require(state['Status'] == 'exited' and state['ExitCode'] == 0, 'Actual runtime native probe failed')
    require(probe['sqlite'] == '3.54.1' and probe['zlib'] == '1.3.2', 'Actual loaded native versions differ')
    for package in lock['packages']:
        loaded = probe['loaded'][package['name']]
        require(len(loaded) == 1 and loaded[0]['path'] in {package['library'], package['soname']}
                and loaded[0]['sha256'] == package['library_sha256'],
                'Actual runtime loads a different native library')
        facts['packages'][package['name']]['loaded_library_sha256'] = loaded[0]['sha256']
    facts['runtime_probe'] = probe
    return facts


def verify_soupsieve_scope(report, facts, profile, runtime):
    from scripts import check_agent_full_scope as scope

    require(profile['name'] == scope.SOUP_PROFILE_NAME and profile['risk_acceptance'] is False,
            'Exact current CSS-path profile required')
    # This pure-Python route does not depend on cold-built CPython ELF bytes.
    # Fixed SQLite/zlib checks below and packaged full-scope ELF checks stay strict.
    require(report['Metadata']['ImageID'] == facts['image_id']
            and runtime['uid'] == 10001
            and runtime['python_source_closure'] == profile['python_source_closure'],
            'Actual immutable image/full Python scope differs from CSS-path review')
    findings = [scope.finding_tuple(result, row) for result in report['Results']
                for row in result.get('Vulnerabilities', [])]
    packages = [scope.package_tuple(result, row) for result in report['Results'] for row in result['Packages']]
    require(Counter(map(scope.canonical, findings)) == Counter(scope.canonical(row['tuple']) for row in profile['findings'])
            and Counter(map(scope.canonical, packages)) == Counter(map(scope.canonical, profile['packages'])),
            'Complete current raw findings/packages changed; do not propagate a CSS-path exception')
    reviewed = [row for row in profile['findings'] if row['tuple']['package'] == 'soupsieve']
    require(len(reviewed) == 2 and {row['tuple']['id'] for row in reviewed} == {'CVE-2026-126190', 'CVE-2026-126192'}
            and all(row['tuple']['version'] == '2.9.2'
                    and row['disposition'] == 'NOT_AFFECTED_FUNCTION_NOT_IN_CURRENT_APPLICATION_PATH'
                    and row['upstream_package_affected'] is True and row['binary_fixed_claim'] is False
                    and row['risk_acceptance'] is False for row in reviewed),
            'Only the two exact affected-but-unreached SoupSieve findings are reviewed')
    return {'image_id': facts['image_id'], 'profile': profile['name'],
            'profile_sha256': scope.profile_identity(profile['name'])[1],
            'python_source_closure': runtime['python_source_closure'], 'findings': reviewed}


def evaluate(report, facts, lock, *, now=None, supplement=None, python_selector_scope=None):
    supplemental_package = validate_supplement(supplement, lock) if supplement is not None else None
    require(report.get('SchemaVersion') == 2 and report.get('Trivy') == {'Version': '0.75.0'}
            and report.get('ArtifactType') == 'container_image', 'Complete supported Trivy report required')
    metadata = report['Metadata']
    require(metadata['ImageID'] == facts['image_id'] and metadata['DiffIDs'] == facts['diff_ids']
            and metadata['OS']['Family'] == 'debian' and metadata['OS']['Name'].split('.')[0] == '13',
            'Scan and actual Debian 13 image identity differ')
    created = datetime.fromisoformat(report['CreatedAt'].replace('Z', '+00:00'))
    age = (now or datetime.now(timezone.utc)) - created
    require(-timedelta(minutes=5) <= age <= timedelta(hours=24), 'Trivy report is stale or in the future')
    records = report.get('Results')
    require(isinstance(records, list) and len(records) == 2
            and Counter((r.get('Class'), r.get('Type')) for r in records)
            == Counter({('os-pkgs', 'debian'): 1, ('lang-pkgs', 'python-pkg'): 1}),
            'Complete OS and Python package coverage required')
    fixed, not_affected, blocked, counts = [], [], [], Counter()
    for record in [report, *records]:
        require(all(key not in record or (isinstance(record[key], list) and not record[key])
                    for key in ('Errors', 'ModifiedFindings', 'SuppressedFindings')),
                'Filtered or incomplete raw scan')
        require('Secrets' not in record or (isinstance(record['Secrets'], list) and not record['Secrets']),
                'Every secret or malformed secret collection blocks')
    for record in records:
        packages = record.get('Packages')
        require(isinstance(packages, list) and len(packages) >= (21 if record['Type'] == 'debian' else 100),
                'Full package inventory is missing')
        inventory = {(x['Name'], (str(x['Epoch']) + ':' if x.get('Epoch') else '') + x['Version']
                      + ('-' + x['Release'] if x.get('Release') else '')) for x in packages}
        vulnerabilities = record.get('Vulnerabilities', [])
        require(isinstance(vulnerabilities, list), 'Malformed vulnerability collection')
        seen = set()
        for finding in vulnerabilities:
            severity = finding['Severity']
            require(severity in LEVELS and (finding['PkgName'], finding['InstalledVersion']) in inventory,
                    'Vulnerability has no matching package or severity')
            key = (finding['VulnerabilityID'], finding['PkgName'], finding['InstalledVersion'], severity)
            require(key not in seen, 'Duplicate finding')
            seen.add(key)
            counts[severity] += 1
            if severity not in {'HIGH', 'CRITICAL'}:
                continue
            if python_selector_scope is not None:
                from scripts.check_agent_full_scope import finding_tuple
                scoped = [row for row in python_selector_scope['findings']
                          if row['tuple'] == finding_tuple(record, finding)]
                if scoped:
                    require(python_selector_scope['image_id'] == facts['image_id'], 'CSS-path proof belongs to another image')
                    not_affected.extend(scoped)
                    continue
            candidates = [p for p in lock['packages'] if record['Type'] == 'debian'
                          and finding['VulnerabilityID'] in p['fixed_advisories']
                          and finding['PkgName'] == p['name'] and finding['InstalledVersion'] == p['version']
                          and finding.get('DataSource', {}).get('ID') == 'debian' and severity == 'HIGH']
            supplemental = False
            if not candidates and supplemental_package is not None:
                exact = (key[0], key[1], key[2], severity, record['Class'], record['Type'],
                         finding.get('DataSource', {}).get('ID'))
                supplemental = any(exact == (row['id'], row['package'], row['version'], row['severity'],
                    row['class'], row['type'], row['data_source']) for row in supplement['findings'])
                if supplemental:
                    candidates = [supplemental_package]
            if len(candidates) == 1:
                p = candidates[0]
                actual = facts['packages'][p['name']]
                require(actual['library_sha256'] == actual['loaded_library_sha256'] == p['library_sha256'],
                        'Unverified library cannot receive a fixed disposition')
                if supplemental:
                    probe = facts['runtime_probe']
                    loaded = probe['loaded'][p['name']]
                    require(probe['sqlite'] == p['upstream'] and len(loaded) == 1
                            and loaded[0]['path'] in {p['library'], p['soname']}
                            and loaded[0]['sha256'] == p['library_sha256'],
                            'Supplement requires the actual uniquely loaded reviewed SQLite ELF')
                disposition = {'id': key[0], 'package': p['name'], 'version': p['version'],
                              'disposition': 'FIXED_IN_VERIFIED_OFFICIAL_LIBRARY',
                              'library_sha256': p['library_sha256']}
                if supplemental:
                    disposition.update(review_kind='FIXED_CODE', risk_acceptance=False,
                                       supplement_sha256=SUPPLEMENT_SHA256)
                fixed.append(disposition)
            else:
                blocked.append({'id': key[0], 'package': finding['PkgName'], 'severity': severity})
    return {'passed': not blocked, 'raw_counts': dict(counts), 'verified_fixed': fixed,
            'unresolved': blocked, 'raw_reports_modified': False,
            'threshold': 'HIGH,CRITICAL; all secrets block; UNKNOWN/LOW/MEDIUM preserved',
            'image_id': facts['image_id'], 'native_lock_sha256': LOCK_SHA256,
            **({'verified_not_affected': not_affected, 'python_selector_scope': python_selector_scope}
               if python_selector_scope is not None else {}),
            **({'native_fixed_code_supplement_sha256': SUPPLEMENT_SHA256} if supplement is not None else {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--scan', required=True, type=Path)
    parser.add_argument('--scanner-exit-code', required=True, type=int)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    result = {'passed': False}
    try:
        require(args.scanner_exit_code == 0, 'Operational scanner failure blocks')
        lock = load_lock()
        supplement = load_supplement(lock)
        facts = collect(args.image, args.output_dir, lock)
        (args.output_dir / 'native-facts.json').write_text(json.dumps(facts, indent=2), encoding='utf-8')
        report = json.loads(args.scan.read_bytes())
        python_scope = None
        if any(row.get('VulnerabilityID') == 'CVE-2026-126190'
               for record in report.get('Results', []) for row in record.get('Vulnerabilities', [])):
            from scripts import check_agent_full_scope as scope
            profile = scope.load_profile(scope.SOUP_PROFILE_NAME)
            runtime = scope.probe_image(facts['image_id'], profile)
            python_scope = verify_soupsieve_scope(report, facts, profile, runtime)
            (args.output_dir / 'python-selector-runtime.json').write_text(json.dumps(runtime, indent=2) + '\n', encoding='utf-8')
        result = evaluate(report, facts, lock, supplement=supplement, python_selector_scope=python_scope)
        result['raw_scan_sha256'] = digest(args.scan)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'error_type': type(error).__name__,
                  'error': str(error) if isinstance(error, ValueError) else 'Required scan/native operation failed'}
    (args.output_dir / 'verdict.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
