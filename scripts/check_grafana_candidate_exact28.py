"""Review a new exact Grafana artifact; pending authorization never passes.

The old exact18 loader and its authorization are deliberately not used. The
shared scanner evaluator is a pure factual helper, surrounded here by complete
exact row and inventory checks and a separate new-subject authorization gate.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import grafana_image_disposition as legacy  # noqa: E402
from scripts.oci_artifact import verify_oci_archive  # noqa: E402

REQUEST_PATH = Path('deployment/security/grafana-candidate-request.json')
AUTH_PATH = Path('deployment/security/grafana-candidate-authorization.json')
# Root fills this only after the request and independent evidence are complete.
# An absent pin cannot be bypassed by preparation mode or an authorization file.
REQUEST_SHA256 = '7ab4c179c5224a4c7acde032640f71b84422120d92ae82ad251c506d37e8381d'
MAX_ARCHIVE_BYTES = 2 * 1024**3
HEX = re.compile(r'[0-9a-f]{64}\Z')
DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
LEVELS = frozenset({'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN'})
BLOCKING = frozenset({'CRITICAL', 'HIGH', 'UNKNOWN'})
POLICY_FLAGS = ('keep_all_raw', 'unknown_or_new_or_failed_or_missing_blocks',
                'any_source_or_binary_or_image_change_invalidates',
                'no_medium_low_exclusions', 'no_broad_module_ignore')
SUBJECT_KEYS = ('artifact', 'archive_bytes', 'archive_sha256', 'index_digest',
                'platform_manifest_digest', 'config_digest', 'diff_ids', 'binary_sha256')
COPIED_SOURCE_DIRS = ('deployment/vendors/grafana/main', 'deployment/vendors/grafana/plugins',
                      'deployment/vendors/grafana-security/main',
                      'deployment/vendors/grafana-security/plugins')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate JSON keys are not evidence')
            result[key] = value
        return result

    def invalid_constant(_value):
        raise ValueError('Nonfinite JSON values are not evidence')

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def file_hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_json(path, limit=64 * 1024**2):
    require(not path.is_symlink() and path.is_file() and 0 < path.stat().st_size <= limit,
            'Missing, linked or oversized JSON evidence')
    return strict_json(path.read_bytes())


def repository_file(root, name):
    require(type(name) is str and name and '\\' not in name, 'Unsafe repository evidence path')
    path = root / name
    require(not Path(name).is_absolute() and '..' not in Path(name).parts
            and path.resolve().is_relative_to(root.resolve()) and not path.is_symlink()
            and path.is_file(), 'Missing or escaping repository evidence')
    return path


def subject_for(binding):
    return {key: binding[key] for key in SUBJECT_KEYS}


def validate_copied_source_inventory(root, binding):
    """A new COPY input is a source change even if every old file still hashes."""
    resolved_root = root.resolve()

    def physical(path):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not (getattr(info, 'st_file_attributes', 0)
                         & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))
                and path.resolve().is_relative_to(resolved_root),
                'Linked or escaping compile-input file/directory is not approved')
        return info.st_mode

    for relative in COPIED_SOURCE_DIRS:
        directory = root / relative
        # Protect the COPY root and every repository-relative ancestor too.
        for parent in (directory, *directory.parents):
            if parent == root:
                break
            require(stat.S_ISDIR(physical(parent)), 'Compile-input ancestor is not a physical directory')
        expected = {name for name in binding['recipe_files'] if name.startswith(relative + '/')}
        require(expected, 'Complete compile-input directory inventory is required')
        actual, pending = set(), [directory]
        while pending:
            current = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    mode = physical(path)
                    if stat.S_ISDIR(mode):
                        pending.append(path)
                    else:
                        require(stat.S_ISREG(mode), 'Unsupported compile-input filesystem node')
                        actual.add(path.relative_to(root).as_posix())
        require(actual == expected, 'Compile-input file set changed; independent re-review is required')


def row_key(row, *, with_disposition=False):
    require(type(row) is dict and all(type(row.get(key)) is str and row[key]
                                    for key in ('id', 'target', 'package', 'version', 'severity'))
            and row['severity'] in BLOCKING, 'Malformed exact blocking row')
    target = legacy.normalized_target(row['target'])
    require(target == row['target'], 'Bound target rows must use canonical relative paths')
    result = (row['id'], target, row['package'], row['version'], row['severity'])
    if with_disposition:
        require(row.get('disposition') in {'FIXED', 'NOT_AFFECTED'}, 'Unsupported exact disposition')
        result += (row['disposition'],)
    return result


def row_counter(rows, expected_count, *, with_disposition=False):
    require(type(rows) is list and len(rows) == expected_count, 'Exact row count differs')
    result = Counter(row_key(row, with_disposition=with_disposition) for row in rows)
    require(len(result) == expected_count and all(count == 1 for count in result.values()),
            'Missing or duplicate exact review rows')
    return result


def canonical_grype(row, binding):
    key = row_key(row)
    aliases = [alias for alias in binding['scanner_exact_aliases']
               if alias['scanner'] == 'Grype' and key == (alias['raw_id'], alias['target'],
                   alias['package'], alias['version'], alias['severity'])]
    require(len(aliases) <= 1, 'Ambiguous scanner alias')
    return (aliases[0]['canonical_id'], *key[1:]) if aliases else key


def validate_contract(binding):
    require(binding['artifact'] == 'grafana' and binding['root_layout'] == 'direct_manifest'
            and binding['archive_limit_bytes'] == MAX_ARCHIVE_BYTES
            and type(binding['archive_bytes']) is int and 0 < binding['archive_bytes'] <= MAX_ARCHIVE_BYTES
            and HEX.fullmatch(binding['archive_sha256'])
            and all(DIGEST.fullmatch(binding[key]) for key in
                    ('index_digest', 'platform_manifest_digest', 'config_digest')),
            'New exact OCI subject is incomplete')
    binaries, layers = binding['binary_sha256'], binding['diff_ids']
    require(type(binaries) is dict and len(binaries) == 14
            and 'usr/share/grafana/bin/grafana' in binaries
            and all(legacy.normalized_target(path) == path and HEX.fullmatch(digest)
                    for path, digest in binaries.items())
            and type(layers) is list and layers and all(DIGEST.fullmatch(value) for value in layers),
            'Complete fourteen-ELF/runtime DiffID identity is required')
    allowed = row_counter(binding['allowed_findings'], 28, with_disposition=True)
    expected_trivy = row_counter(binding['exact_trivy_rows'], 28)
    require(Counter(key[:-1] for key in allowed) == expected_trivy,
            'Exact28 and requested disposition identities differ')
    expected_grype = row_counter(binding['exact_grype_rows'], 7)
    aliases = binding['scanner_exact_aliases']
    require(type(aliases) is list and len(aliases) == 2
            and {(a['raw_id'], a['canonical_id']) for a in aliases} == {
                ('GHSA-p4r4-xvrq-gvmc', 'CVE-2026-21728'),
                ('GHSA-ffqx-q65f-36jf', 'CVE-2026-28377')}
            and all(a['scanner'] == 'Grype' and a['severity'] == 'HIGH'
                    and (a['raw_id'], a['target'], a['package'], a['version'], a['severity'])
                    in expected_grype for a in aliases), 'Unreviewed or broad scanner alias')
    require(all(canonical_grype(row, binding) in expected_trivy for row in binding['exact_grype_rows']),
            'Grype rows do not map to the exact new reviewed subjects')
    coverage, inventory = binding['exact_trivy_coverage'], binding['exact_trivy_inventory']
    require(type(coverage) is dict and set(coverage) == {'alpine', 'Node.js', *binaries}
            and type(inventory) is dict and set(inventory) == set(coverage),
            'Complete sixteen-target scan inventory is required')
    for target, count in coverage.items():
        require(type(count) is int and count > 0 and type(inventory[target]) is list
                and len(inventory[target]) == count, 'Exact target package count differs')
        package_counter(inventory[target], bound=True)
    require(type(binding['exact_syft_types']) is dict and binding['exact_syft_types']
            and all(type(value) is int and value > 0 for value in binding['exact_syft_types'].values()),
            'Exact full native SBOM type coverage is required')


def load_request(root=ROOT, now=None):
    require(type(REQUEST_SHA256) is str and HEX.fullmatch(REQUEST_SHA256),
            'Final independent request SHA256 pin is not set')
    path = repository_file(root, REQUEST_PATH.as_posix())
    require(file_hash(path) == REQUEST_SHA256, 'New request bytes changed')
    request = read_json(path)
    require(request['schema'] == 'deepresearch-grafana-candidate-request-v1'
            and request['technical_ready'] is True, 'New technical request is incomplete')
    binding = request['binding']
    require(request['expires'] == binding['expires'], 'New request expiry differs')
    expiry, current = datetime.fromisoformat(binding['expires']), now or datetime.now(timezone.utc)
    require(expiry.tzinfo is not None and current.tzinfo is not None and current < expiry,
            'New exact-subject request expired or clock is invalid')
    files = request['files_sha256']
    require(type(files) is dict and files and REQUEST_PATH.as_posix() not in files
            and AUTH_PATH.as_posix() not in files, 'Evidence binding is empty or circular')
    for name, digest in files.items():
        require(type(digest) is str and HEX.fullmatch(digest)
                and file_hash(repository_file(root, name)) == digest,
                'New source, recipe, raw evidence or independent review bytes changed')
    validate_copied_source_inventory(root, binding)
    reviews = request['independent_reviews']
    require(type(reviews) is list and len(reviews) >= 2
            and len({row['path'] for row in reviews}) == len(reviews),
            'Complete independent new-subject reviews are required')
    for review in reviews:
        require(review['path'] != 'deployment/security/grafana-fixed-evidence/independent-review.json'
                and files.get(review['path']) == review['sha256'],
                'Old exact18 review cannot replace a newly bound independent review')
        require(type(read_json(repository_file(root, review['path']))) is dict,
                'Independent review has no structured factual record')
    for name, digest in (binding['evidence_files'] | binding['recipe_files']).items():
        require(files.get(name) == digest, 'Legacy factual-helper input is not in the new file binding')
    for row in binding['inventory_identity']['local_source_modules']:
        require(row['source_go_mod'] in files and row['replace_file'] in files,
                'Local source module proof is outside the new file binding')
    validate_contract(binding)
    legacy.validate_local_sources(binding, root)
    return request


def empty_filter_markers(record):
    for name in ('Errors', 'ModifiedFindings', 'SuppressedFindings'):
        require(name not in record or (type(record[name]) is list and not record[name]),
                'Incomplete, filtered or suppressed scanner evidence')


def package_counter(packages, *, bound=False):
    require(type(packages) is list, 'Malformed package inventory')
    result = Counter()
    for package in packages:
        require(type(package) is dict, 'Malformed package record')
        name, version = (package.get('name'), package.get('version')) if bound else (
            package.get('Name'), package.get('Version'))
        require(type(name) is str and name and (version is None or
                (type(version) is str and version and version not in {'unknown', '(devel)'})),
                'Unknown package identity is outside the reviewed source mapping')
        result[(name, version)] += 1
    return result


def verify_full_scans(trivy, grype, sbom, inspection, binding, *, trivy_exit_code, grype_exit_code):
    require(type(trivy_exit_code) is int and trivy_exit_code == 0
            and type(grype_exit_code) is int and grype_exit_code == 0,
            'Full operational scanner success is required')
    require(type(trivy) is dict and trivy.get('SchemaVersion') == 2
            and trivy.get('ArtifactType') == 'container_image'
            and trivy.get('Trivy') == {'Version': '0.75.0'}
            and type(trivy.get('Results')) is list and len(trivy['Results']) == 16,
            'Complete supported Trivy runtime report is required')
    empty_filter_markers(trivy)
    coverage, observed, targets = {}, Counter(), set()
    for record in trivy['Results']:
        require(type(record) is dict and type(record.get('Target')) is str and record['Target'],
                'Malformed Trivy target')
        empty_filter_markers(record)
        for name in ('Packages', 'Vulnerabilities', 'Secrets'):
            require(name not in record or type(record[name]) is list, 'Malformed Trivy collection')
        require(not record.get('Secrets'), 'Every secret blocks; matched values are never emitted')
        key = record['Type'] if record.get('Class') == 'os-pkgs' else record['Target']
        require(key not in coverage and key in binding['exact_trivy_coverage'],
                'Missing, repeated or additional Trivy inventory target')
        expected_type = ('os-pkgs', 'alpine') if key == 'alpine' else (
            ('lang-pkgs', 'node-pkg') if key == 'Node.js' else ('lang-pkgs', 'gobinary'))
        require((record.get('Class'), record['Type']) == expected_type, 'Trivy target type changed')
        coverage[key] = len(record['Packages'])
        inventory = package_counter(record['Packages'])
        require(inventory == package_counter(binding['exact_trivy_inventory'][key], bound=True),
                'Actual complete target package identity differs')
        if record['Type'] == 'gobinary':
            targets.add(record['Target'])
        for finding in record.get('Vulnerabilities', []):
            require(type(finding) is dict and finding.get('Severity') in LEVELS
                    and all(type(finding.get(name)) is str and finding[name] for name in
                            ('VulnerabilityID', 'PkgName', 'InstalledVersion')),
                    'Malformed Trivy vulnerability identity')
            require((finding['PkgName'], finding['InstalledVersion']) in inventory,
                    'Vulnerability has no matching native target package')
            if finding['Severity'] in BLOCKING:
                observed[(finding['VulnerabilityID'], legacy.normalized_target(record['Target']),
                          finding['PkgName'], finding['InstalledVersion'], finding['Severity'])] += 1
    require(coverage == binding['exact_trivy_coverage'] and targets == set(binding['binary_sha256']),
            'Complete package/ELF coverage differs')
    require(observed == row_counter(binding['exact_trivy_rows'], 28),
            'Exact28 blocking findings are missing, extra, duplicate or changed')
    require(type(grype) is dict and type(grype.get('matches')) is list
            and ('ignoredMatches' not in grype or
                 (type(grype['ignoredMatches']) is list and not grype['ignoredMatches'])),
            'Complete unsuppressed Grype report is required')
    empty_filter_markers(grype)
    grype_rows = Counter()
    for match in grype['matches']:
        require(type(match) is dict and type(match.get('vulnerability')) is dict
                and type(match.get('artifact')) is dict, 'Malformed Grype finding')
        vulnerability, artifact = match['vulnerability'], match['artifact']
        severity = vulnerability.get('severity')
        require(severity in {'Critical', 'High', 'Medium', 'Low', 'Unknown', 'Negligible'},
                'Unknown Grype severity')
        if severity.upper() in BLOCKING:
            require(type(artifact.get('locations')) is list and artifact['locations'],
                    'Blocking Grype target identity is missing')
            for location in artifact['locations']:
                row = {'id': vulnerability.get('id'), 'target': legacy.normalized_target(location['path']),
                       'package': artifact.get('name'), 'version': artifact.get('version'),
                       'severity': severity.upper()}
                grype_rows[row_key(row)] += 1
    require(grype_rows == row_counter(binding['exact_grype_rows'], 7),
            'Exact7 native blocking findings are missing, extra, duplicate or changed')
    require(type(sbom) is dict and type(sbom.get('artifacts')) is list and sbom['artifacts'],
            'Complete native SBOM is required')
    source = sbom['source']['metadata']
    # This native Docker manifest may differ from the OCI transport manifest.
    # Bind its exact config bytes/DiffIDs, rather than mixing those identities.
    strict_json(base64.b64decode(source['config'], validate=True))
    strict_json(base64.b64decode(source['manifest'], validate=True))
    require(Counter(artifact['type'] for artifact in sbom['artifacts'])
            == Counter(binding['exact_syft_types']), 'Complete native SBOM coverage changed')
    result = legacy.evaluate_scans(trivy, grype, sbom, inspection, binding,
        trivy_exit_code=trivy_exit_code, grype_exit_code=grype_exit_code)
    require(result['passed'] is True and result['unresolved_count'] == 0
            and len(result['verified_dispositions']['Trivy']) == 28
            and len(result['verified_dispositions']['Grype']) == 7,
            'Pure scanner evaluation does not match the complete exact subject')
    return result


def validate_authorization(auth, request):
    binding = request['binding']
    require(type(auth) is dict and auth.get('accepted') is True,
            'New exact-subject human authorization is pending')
    require(auth.get('request_sha256') == REQUEST_SHA256
            and auth.get('subject') == subject_for(binding), 'Authorization binds another request or subject')
    require(row_counter(auth['exact_target_advisory_rows'], 28, with_disposition=True)
            == row_counter(binding['allowed_findings'], 28, with_disposition=True),
            'New authorization exact28 scope differs')
    policy = auth['policy']
    require(type(policy) is dict and policy.get('expires') == binding['expires']
            and all(policy.get(flag) is True for flag in POLICY_FLAGS),
            'New authorization policy/expiry differs')


def final_result(facts, request, auth=None, *, prepare=False):
    result = {**facts, 'passed': False, 'technical_ready': True, 'approval_pending': True,
              'old_authorization_inherited': False, 'raw_reports_modified': False,
              'request_sha256': REQUEST_SHA256,
              'mode': 'new_exact28_facts_pending_human_authorization'}
    if prepare:
        result['mode'] = 'new_exact28_preparation_only_never_authorizes'
        return result
    validate_authorization(auth, request)
    return {**result, 'passed': True, 'approval_pending': False,
            'mode': 'new_human_authorized_exact28_Grafana_artifact'}


def validate_output(args):
    cache = (ROOT / '.cache/eval').resolve()
    output = args.output.resolve()
    require(cache.is_relative_to(ROOT.resolve()) and output.is_relative_to(cache)
            and not args.output.exists() and not args.output.is_symlink()
            and output not in {getattr(args, name).resolve() for name in
                               ('archive', 'scan', 'grype_scan', 'sbom', 'inspect')},
            'Output must be a distinct new workspace .cache/eval file')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('archive', 'scan', 'grype-scan', 'sbom', 'inspect', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--scanner-exit-code', required=True, type=int)
    parser.add_argument('--grype-exit-code', required=True, type=int)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args(argv)
    try:
        validate_output(args)
        request = load_request(ROOT)
        binding = request['binding']
        archive = verify_oci_archive(args.archive, binding, max_archive_bytes=MAX_ARCHIVE_BYTES)
        inspection = read_json(args.inspect)
        if type(inspection) is list:
            require(len(inspection) == 1, 'Exactly one actual image inspection is required')
            inspection = inspection[0]
        facts = verify_full_scans(read_json(args.scan), read_json(args.grype_scan), read_json(args.sbom),
            inspection, binding, trivy_exit_code=args.scanner_exit_code, grype_exit_code=args.grype_exit_code)
        facts.update(archive=archive, raw_scan_sha256=file_hash(args.scan),
                     raw_grype_sha256=file_hash(args.grype_scan), raw_sbom_sha256=file_hash(args.sbom))
        auth_path = ROOT / AUTH_PATH
        auth = read_json(auth_path) if not args.prepare and auth_path.is_file() else None
        result = final_result(facts, request, auth, prepare=args.prepare)
    except (ValueError, TypeError, KeyError, OSError, IndexError, AttributeError,
            subprocess.SubprocessError, tarfile.TarError) as error:
        result = {'passed': False, 'mode': 'failed_closed', 'error_type': type(error).__name__}
        if isinstance(error, ValueError):
            result['error'] = str(error)[:220]
    try:
        validate_output(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(result, indent=2) + '\n')
    except (ValueError, OSError):
        print(json.dumps({'passed': False, 'mode': 'failed_closed', 'error': 'Unsafe output path'}))
        return 1
    print(json.dumps({key: result[key] for key in ('passed', 'mode', 'technical_ready', 'approval_pending')
                      if key in result}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
