"""Exact source-fixed Keycloak zlib; retain all original reports and JVM checks."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_native_jvm as native  # noqa: E402
from scripts.check_image_secrets import assess as secrets  # noqa: E402

CVE = 'CVE-2026-76844'
VERSION = '1.4-1.deepresearch.el9'
LIBRARY = '41a206fc2715141be96b459b49403e1a94cd642c56b5b401283913573481d8ba'
SOURCE = '02dd159783677a749c04adbdd02b55b8a2bcc7e054f51b76b549199a916ad584'
NATIVE_CONSUMER = '3081ed1f47765ac2fc60f64d1482099b312331f0fb7626ef7b1472ba2fcd0de6'

def require(value, message):
    if not value:
        raise ValueError(message)

def full_counts(report, sbom):
    require(report['descriptor']['name'] == 'grype' and report['descriptor']['version'] == '0.121.0'
            and report['descriptor']['db']['status']['valid'] is True and not report.get('ignoredMatches'), 'Invalid full report')
    require(report['source']['target']['imageID'] == sbom['source']['metadata']['imageID'], 'Grype image differs')
    counts, fixed = Counter(), 0
    for row in report['matches']:
        v, p = row['vulnerability'], row['artifact']
        require(v['severity'] in {'Low', 'Medium', 'High', 'Critical', 'Unknown', 'Negligible'}, 'Unknown severity')
        counts[v['severity']] += 1
        if v['severity'] in {'High', 'Critical', 'Unknown'}:
            require(v['id'] == CVE and v['severity'] == 'High' and v['namespace'] == 'redhat:distro:redhat:9'
                    and p['name'] == 'zlib' and p['version'] == VERSION and p['type'] == 'rpm', 'Unreviewed blocking tuple')
            fixed += 1
    require(fixed <= 1, 'Expanded fixed-library tuple')
    return dict(counts), fixed


def assess(scan, image, directory, native_exit, output):
    require(native.digest(Path(native.__file__)) == NATIVE_CONSUMER, 'Native consumer changed')
    inspection = json.loads(native.run(['docker', 'image', 'inspect', image]))[0]
    require(secrets(scan)['passed'], 'Image secret finding')
    require(scan['Metadata']['ImageID'] == inspection['Id']
            and scan['Metadata']['DiffIDs'] == inspection['RootFS']['Layers'], 'Trivy identity differs')
    require(any(r.get('Class') == 'os-pkgs' and r.get('Type') == 'redhat' and r.get('Packages') for r in scan['Results'])
            and any(r.get('Class') == 'lang-pkgs' and r.get('Type') == 'jar' and r.get('Packages') for r in scan['Results']), 'Complete OS and Java Trivy inventory required')
    summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    require(summary['actual_image_id'] == inspection['Id'] and summary['profile'] == 'keycloak', 'Native identity differs')
    require(native_exit in (0, 1) and summary['passed'] == (native_exit == 0), 'Native exit differs')
    if native_exit:
        require(summary.get('error_type') == 'NativeGateError'
                and summary.get('error') == 'Native/full image scan has blocking HIGH/CRITICAL/UNKNOWN findings', 'Unrelated native failure')
    lock = json.loads(native.LOCK.read_text(encoding='utf-8'))
    profile = lock['profiles']['keycloak']
    manifest = ROOT / profile['file_manifest']
    require(native.digest(manifest) == profile['file_manifest_sha256'], 'JRE manifest changed')
    entries = json.loads(manifest.read_text(encoding='utf-8'))['entries']
    require(summary['actual_JRE_bytes'] == {'verified_entries': len(entries), 'verified_regular_bytes': sum(x.get('bytes', 0) for x in entries.values())}, 'Original actual JRE check missing')
    reports = {}
    for mode in ('native', 'full'):
        sbom_path, report_path = directory / (mode + '-syft.json'), directory / (mode + '-grype.json')
        sbom, report = json.loads(sbom_path.read_text(encoding='utf-8')), json.loads(report_path.read_text(encoding='utf-8'))
        native.validate_source(sbom, inspection)
        if mode == 'native':
            require(native.validate_jvm(sbom, profile, lock['expected_jvm']) == summary['JVM'], 'JVM differs')
            require(native.validate_scan(report, sbom) == summary['native']['raw_counts']
                    and native.digest(sbom_path) == summary['native']['sbom_sha256']
                    and native.digest(report_path) == summary['native']['report_sha256'], 'Native receipt differs')
        else:
            native.validate_full_inventory(sbom, profile)
        reports[mode] = (sbom, report)
    database = summary['database']
    native.validate_snapshot_age(database['snapshot'])
    derived = database['derived_database']
    require(Path(derived['path']).stat().st_size == derived['bytes'] and native.digest(Path(derived['path'])) == derived['sha256'], 'Derived database changed')
    require(database['derived_integrity_checks'] == [dict(stage=s, bytes=derived['bytes'], sha256=derived['sha256']) for s in ('after-status', 'after-native-scan', 'after-full-scan')], 'Database stages incomplete')
    container = summary['created_audit_container_retained']
    actual = json.loads(native.run(['docker', 'container', 'inspect', container]))[0]
    require(actual['Image'] == inspection['Id'] and not actual['State']['Running']
            and actual['Config']['Labels'].get('com.deepresearch.purpose') == 'native-jvm-inventory'
            and actual['HostConfig']['NetworkMode'] == 'none' and not actual['Mounts'], 'Audit container differs')
    output.mkdir(parents=True, exist_ok=False)
    library, source = output / 'libz.so.1.4', output / 'zlib-1.4.tar.gz'
    native.run(['docker', 'cp', container + ':/usr/lib64/libz.so.1.4', str(library)])
    native.run(['docker', 'cp', container + ':/usr/share/deepresearch/keycloak-zlib/zlib-1.4.tar.gz', str(source)])
    require(native.digest(library) == LIBRARY and native.digest(source) == SOURCE, 'Actual fixed payload differs')
    with tarfile.open(source) as archive:
        code = archive.extractfile('zlib-1.4/gzwrite.c').read().decode('utf-8')
    require(re.search(r'if \(ret == -1\)\s*\{\s*state->strm\.avail_in = 0;\s*state->strm\.next_in = state->in;', code), 'Official source pointer reset missing')
    sbom, full = reports['full']
    packages = [a for a in sbom['artifacts'] if a['name'] == 'zlib' and a['type'] == 'rpm']
    require(len(packages) == 1 and packages[0]['version'] == VERSION
            and any(f.get('path') == '/usr/lib64/libz.so.1.4' and f.get('digest') == {'algorithm': 'sha256', 'value': LIBRARY} for f in packages[0]['metadata']['files']), 'RPM inventory does not own fixed library')
    counts, fixed = full_counts(full, sbom)
    require(native_exit == int(bool(fixed)), 'Original native outcome differs')
    trivy_counts, trivy_fixed = Counter(), 0
    for result in scan['Results']:
        for row in result.get('Vulnerabilities', []):
            level = row['Severity']
            require(level in {'LOW', 'MEDIUM', 'HIGH', 'CRITICAL', 'UNKNOWN'}, 'Unknown Trivy severity')
            trivy_counts[level] += 1
            if level in {'HIGH', 'CRITICAL', 'UNKNOWN'}:
                require(level == 'HIGH' and row['VulnerabilityID'] == CVE and row['PkgName'] == 'zlib'
                        and row['InstalledVersion'] == VERSION and result['Class'] == 'os-pkgs'
                        and result['Type'] == 'redhat', 'Unreviewed Trivy blocking tuple')
                trivy_fixed += 1
    require(trivy_fixed <= 1, 'Expanded Trivy fixed tuple')
    after = json.loads(native.run(['docker', 'image', 'inspect', image]))[0]
    require(all(after[k] == inspection[k] for k in ('Id', 'Config', 'RootFS')), 'Image changed during verification')
    return {'passed': True, 'disposition': 'FIXED_IN_EXACT_UBI9_BUILT_OFFICIAL_ZLIB_1_4', 'risk_acceptance': False,
            'raw_zero_claim': False, 'all_lower_severity_findings_reviewed': False,
            'image_id': inspection['Id'], 'portable_config_digest': sbom['source']['metadata']['imageID'],
            'diff_ids': inspection['RootFS']['Layers'], 'runtime_config': inspection['Config'],
            'library_sha256': LIBRARY, 'source_sha256': SOURCE, 'trivy_raw_counts': dict(trivy_counts),
            'grype_full_raw_counts': counts, 'source_fixed_rows': {'trivy': trivy_fixed, 'grype': fixed},
            'original_native_exit_code': native_exit, 'original_native_summary_sha256': native.digest(directory / 'summary.json'),
            'actual_JRE_bytes': summary['actual_JRE_bytes'], 'services_started': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan', required=True, type=Path)
    parser.add_argument('--image', required=True)
    parser.add_argument('--native-dir', required=True, type=Path)
    parser.add_argument('--native-exit-code', required=True, type=int)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        verdict = assess(json.loads(args.scan.read_text(encoding='utf-8')), args.image, args.native_dir,
                         args.native_exit_code, args.output.parent / 'actual-fixed-payload')
        verdict['raw_scan_sha256'] = native.digest(args.scan)
    except Exception as error:
        verdict = {'passed': False, 'error_type': type(error).__name__, 'error': str(error)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(verdict, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in verdict.items() if k != 'runtime_config'}))
    return 0 if verdict['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
