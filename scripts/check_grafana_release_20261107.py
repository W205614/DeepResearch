"""Independent new exact release consumer; no old scope or authority is inherited."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import check_grafana_candidate_exact28 as shared  # noqa: E402
from scripts import check_grafana_maintenance as old_maintenance  # noqa: E402
from scripts.grafana_release_build_identity import verify_build_identity, SOURCE_COMMIT  # noqa: E402

REQUEST_PATH = Path('deployment/security/grafana-fixed-20261107-request.json')
AUTH_PATH = Path('deployment/security/grafana-fixed-20261107-authorization.json')
# A real independently reviewed request and real published transport must fill
# these data pins. Missing values intentionally fail before HTTP or Docker.
REQUEST_SHA256 = 'd9d2403921ed8f683aa4996a6c4a74dc892e1eac5290f6a3543afaf33a0e8d96'
TRANSPORT_PATH = Path('deployment/security/grafana-fixed-20261107-transport.json')
TRANSPORT_SHA256 = None
PUBLICATION_PATH = Path('deployment/security/grafana-fixed-20261107-publication.json')
PUBLICATION_SHA256 = None
RELEASE_TAG = 'artifact-grafana-fixed-20261107'
RELEASE_SOURCE_COMMIT = SOURCE_COMMIT
APPROVED_TAG = 'deepresearch-approved-grafana:20261107'
SCAN_TAG = 'deepresearch-scan-grafana:20261107'
PROFILE = 'fixed20261107'
CONSUMER_SCRIPT = 'check_grafana_release_20261107.py'
MAX_ARCHIVE_BYTES = shared.MAX_ARCHIVE_BYTES
HEX, DIGEST = shared.HEX, shared.DIGEST
require, read_json, repository_file = shared.require, shared.read_json, shared.repository_file
file_hash, strict_json, subject_for = shared.file_hash, shared.strict_json, shared.subject_for


def expected_counter(binding, name, *, disposition=False):
    rows = binding[name]
    require(type(rows) is list, 'New exact finding scope is missing')
    return shared.row_counter(rows, len(rows), with_disposition=disposition)


def validate_contract(binding):
    require(binding['artifact'] == 'grafana' and binding['root_layout'] == 'nested_index'
            and binding['approved_tag'] == APPROVED_TAG and binding['scope_id'] == PROFILE
            and type(binding['archive_bytes']) is int and 0 < binding['archive_bytes'] <= MAX_ARCHIVE_BYTES
            and binding['archive_limit_bytes'] == MAX_ARCHIVE_BYTES and HEX.fullmatch(binding['archive_sha256'])
            and all(DIGEST.fullmatch(binding[k]) for k in ('index_digest', 'platform_manifest_digest', 'config_digest')),
            'The independent release subject is incomplete')
    binaries = binding['binary_sha256']
    require(type(binaries) is dict and len(binaries) == 14 and 'usr/share/grafana/bin/grafana' in binaries
            and all(shared.legacy.normalized_target(n) == n and HEX.fullmatch(s) for n, s in binaries.items())
            and type(binding['diff_ids']) is list and binding['diff_ids']
            and all(DIGEST.fullmatch(s) for s in binding['diff_ids']), 'Full fourteen-ELF/config/layer identity differs')
    allowed = expected_counter(binding, 'allowed_findings', disposition=True)
    trivy, grype = expected_counter(binding, 'exact_trivy_rows'), expected_counter(binding, 'exact_grype_rows')
    require(len(allowed) == 18 and len(trivy) == 18 and len(grype) == 7
            and all(r['disposition'] == 'NOT_AFFECTED' and r.get('review_disposition') == 'NOT_APPLICABLE'
                    for r in binding['allowed_findings']), 'Only the independently reviewed new eighteen/seven scope is supported')
    aliases = binding['scanner_exact_aliases']
    require(type(aliases) is list and len(aliases) == 2
            and {(r['raw_id'], r['canonical_id']) for r in aliases} == {
                ('GHSA-p4r4-xvrq-gvmc', 'CVE-2026-21728'), ('GHSA-ffqx-q65f-36jf', 'CVE-2026-28377')},
            'Only the two actual new Tempo aliases are reviewed')
    alias_keys = set()
    for alias in aliases:
        key = tuple(alias[k] for k in ('raw_id', 'target', 'package', 'version', 'severity'))
        require(alias['scanner'] == 'Grype' and key in grype and key not in alias_keys
                and alias['canonical_id'] != alias['raw_id'], 'Unobserved, repeated or broad scanner alias')
        alias_keys.add(key)
    canonical_grype = Counter(shared.canonical_grype(row, binding) for row in binding['exact_grype_rows'])
    require(set(key[:-1] for key in allowed) == set(trivy) | set(canonical_grype),
            'Requested dispositions do not equal the complete new scanner scope')
    coverage, inventory = binding['exact_trivy_coverage'], binding['exact_trivy_inventory']
    require(set(coverage) == set(inventory) == {'alpine', 'Node.js', *binaries}, 'Full sixteen-target inventory is required')
    for target, count in coverage.items():
        require(type(count) is int and count > 0 and len(inventory[target]) == count, 'Exact target package coverage differs')
        shared.package_counter(inventory[target], bound=True)
    require(binding['expected_native_application']['version'] == '13.2.5'
            and type(binding['exact_syft_types']) is dict and binding['exact_syft_types']
            and all(type(n) is int and n > 0 for n in binding['exact_syft_types'].values()),
            'New application/full native inventory is missing')


def load_request(root=ROOT):
    require(type(REQUEST_SHA256) is str and HEX.fullmatch(REQUEST_SHA256), 'The independently reviewed new request pin is pending')
    path = repository_file(root, REQUEST_PATH.as_posix())
    require(file_hash(path) == REQUEST_SHA256, 'The new release request bytes changed')
    request = read_json(path)
    require(request['schema'] == 'deepresearch-grafana-candidate-request-v1'
            and request['technical_ready'] is True and request['validity'] == 'change-driven',
            'New final exact-subject technical review is incomplete')
    binding, files = request['binding'], request['files_sha256']
    require(request['expires'] == binding['expires'] == 'change-driven', 'The exact change-driven policy differs')
    identity_path = binding['build_identity_file']
    require(REQUEST_PATH.as_posix() not in files and AUTH_PATH.as_posix() not in files
            and identity_path in files and files, 'The new file binding is missing or circular')
    identity = read_json(repository_file(root, identity_path))
    verify_build_identity(root, identity)
    require(binding['recipe_files'] == identity['committed_input_sha256'], 'Declared recipe differs from the c6 committed input map')
    for name, expected in files.items():
        require(type(expected) is str and HEX.fullmatch(expected), 'A file identity is malformed')
        if name in identity['committed_input_sha256']:
            require(expected == identity['committed_input_sha256'][name], 'Compiled input reference changed')
        else:
            require(file_hash(repository_file(root, name)) == expected, 'Bound evidence/review bytes changed')
    require(all(files.get(n) == s for n, s in (binding['recipe_files'] | binding['evidence_files']).items()),
            'Compiled source or evidence is outside the independently pinned request')
    shared.validate_copied_source_inventory(root, binding, copied_dirs=(
        'deployment/vendors/grafana/main', 'deployment/vendors/grafana-20261107/main',
        'deployment/vendors/grafana-20261107/plugins'))
    verify_reviews(root, request)
    validate_contract(binding)
    shared.legacy.validate_local_sources(binding, root)
    return request


def verify_reviews(root, request):
    binding, files, reviews = request['binding'], request['files_sha256'], request['independent_reviews']
    require(type(reviews) is list and len(reviews) >= 2, 'Two independently bound new-subject reviews are required')
    require(type(request.get('candidate_author')) is str and request['candidate_author'], 'Candidate authorship must be explicit')
    actors = set()
    allowed = binding['allowed_findings']
    expected = shared.row_counter(allowed, 18)
    require(all(r['disposition'] == 'NOT_AFFECTED' and r.get('review_disposition') == 'NOT_APPLICABLE' for r in allowed),
            'Only explicit NOT_APPLICABLE to NOT_AFFECTED naming is supported')
    for ref in reviews:
        require(files.get(ref['path']) == ref['sha256'], 'Independent review is outside the file binding')
        review = read_json(repository_file(root, ref['path']))
        require(review['facts_passed'] is True and review['artifact_approved'] is False
                and review['human_risk_acceptance'] is False and review['actor_kind'] == 'automated_independent_agent'
                and type(review['actor']) is str and review['actor'].startswith('/root/') and review['actor'] not in actors
                and review['actor'] != request['candidate_author']
                and review['subject'] == subject_for(binding), 'Old or different-subject facts cannot authorize this release')
        require(shared.row_counter(review['rows'], 18) == expected
                and shared.row_counter(review['exact_trivy_rows'], 18) == expected_counter(binding, 'exact_trivy_rows')
                and shared.row_counter(review['exact_grype_rows'], 7) == expected_counter(binding, 'exact_grype_rows')
                and Counter(json.dumps(a, sort_keys=True) for a in review['scanner_exact_aliases'])
                    == Counter(json.dumps(a, sort_keys=True) for a in binding['scanner_exact_aliases']),
                'Independent exact rows/native rows/aliases differ from this new request')
        for row in review['rows']:
            require(row['disposition'] == 'NOT_APPLICABLE'
                    and row['subject_elf_sha256'] == binding['binary_sha256'][row['target']]
                    and type(row.get('technical_basis')) is str and row['technical_basis']
                    and type(row.get('evidence')) is list and row['evidence'],
                    'Each exact reviewed row must retain its new-subject evidence and technical basis')
            for evidence in row['evidence']:
                require(files.get(evidence['path']) == evidence['sha256']
                        and file_hash(repository_file(root, evidence['path'])) == evidence['sha256'],
                        'Independent row evidence reference bytes changed')
        actors.add(review['actor'])
    return actors


def verify_sbom_local_versions(sbom, binding):
    local = Counter()
    for row in binding['inventory_identity']['local_source_modules']:
        directive = row['module_directive'].split()
        require(len(directive) == 2 and directive[0] == 'module'
                and row['elf_sha256'] == binding['binary_sha256'].get(row['target']),
                'Exact local SBOM source/ELF identity differs')
        local[(row['target'], directive[1])] += 1
    require(len(local) == 28 and all(n == 1 for n in local.values()),
            'Only the exact twenty-eight local SBOM identities are supported')
    observed = Counter()
    for artifact in sbom['artifacts']:
        version = artifact.get('version')
        require(type(version) is str and version.strip(), 'Full SBOM package version is absent')
        if version.strip().casefold() in {'unknown', '(devel)', 'devel'}:
            locations = artifact.get('locations')
            require(version == 'UNKNOWN' and artifact.get('type') == 'go-module'
                    and type(locations) is list and len(locations) == 1,
                    'Only the exact literal UNKNOWN local Go-module version is reviewed')
            key = (shared.legacy.normalized_target(locations[0]['path']), artifact['name'])
            require(key in local, 'Unknown SBOM target/module is outside the exact source binding')
            observed[key] += 1
    require(observed == local, 'Exact local SBOM identities are missing, extra, duplicate or changed')


def verify_full_scans(trivy, grype, sbom, inspection, binding, *, trivy_exit_code, grype_exit_code):
    now = datetime.now(timezone.utc)
    old_maintenance.fresh_timestamp(trivy.get('CreatedAt'), timedelta(hours=24), now=now)
    descriptor = grype.get('descriptor', {})
    require(descriptor.get('name') == 'grype' and descriptor.get('version') == '0.121.0', 'Pinned Grype identity differs')
    old_maintenance.fresh_timestamp(descriptor.get('timestamp'), timedelta(hours=24), now=now)
    old_maintenance.fresh_timestamp(descriptor.get('db', {}).get('status', {}).get('built'), timedelta(days=7), now=now)
    require(type(trivy_exit_code) is int and trivy_exit_code == 0 and type(grype_exit_code) is int and grype_exit_code == 0,
            'Both full scanners must finish operationally')
    require(trivy.get('SchemaVersion') == 2 and trivy.get('ArtifactType') == 'container_image'
            and trivy.get('Trivy') == {'Version': '0.75.0'} and len(trivy['Results']) == 16,
            'The supported complete Trivy report is required')
    shared.empty_filter_markers(trivy)
    coverage, observed = {}, Counter()
    for record in trivy['Results']:
        shared.empty_filter_markers(record)
        require(not record.get('Secrets'), 'Every secret blocks the independent release')
        key = record['Type'] if record.get('Class') == 'os-pkgs' else record['Target']
        require(key in binding['exact_trivy_coverage'] and key not in coverage, 'Extra or duplicate Trivy target')
        kind = ('os-pkgs', 'alpine') if key == 'alpine' else (('lang-pkgs', 'node-pkg') if key == 'Node.js' else ('lang-pkgs', 'gobinary'))
        require((record.get('Class'), record['Type']) == kind, 'Exact target type differs')
        packages = shared.package_counter(record['Packages'])
        require(packages == shared.package_counter(binding['exact_trivy_inventory'][key], bound=True), 'Complete package identity differs')
        coverage[key] = len(record['Packages'])
        for row in record.get('Vulnerabilities', []):
            require(row.get('Severity') in shared.LEVELS, 'Unsupported scanner severity')
            if row['Severity'] in shared.BLOCKING:
                require((row['PkgName'], row['InstalledVersion']) in packages, 'Finding lacks its actual package')
                observed[(row['VulnerabilityID'], shared.legacy.normalized_target(record['Target']),
                          row['PkgName'], row['InstalledVersion'], row['Severity'])] += 1
    require(coverage == binding['exact_trivy_coverage'] and observed == expected_counter(binding, 'exact_trivy_rows'),
            'New exact Trivy rows/coverage are missing, extra, duplicate or changed')
    shared.empty_filter_markers(grype)
    require(type(grype.get('matches')) is list and not grype.get('ignoredMatches'), 'Complete unsuppressed Grype report is required')
    native = Counter()
    for match in grype['matches']:
        vuln, artifact = match['vulnerability'], match['artifact']
        require(vuln.get('severity') in {'Critical', 'High', 'Medium', 'Low', 'Unknown', 'Negligible'}, 'Unknown Grype severity')
        if vuln['severity'].upper() in shared.BLOCKING:
            require(artifact.get('locations'), 'Blocking native finding lacks exact target locations')
            for location in artifact['locations']:
                native[(vuln['id'], shared.legacy.normalized_target(location['path']), artifact['name'],
                        artifact['version'], vuln['severity'].upper())] += 1
    require(native == expected_counter(binding, 'exact_grype_rows'), 'New exact native rows are missing, extra, duplicate or changed')
    require(Counter(a['type'] for a in sbom['artifacts']) == Counter(binding['exact_syft_types']), 'Full Syft inventory type counts differ')
    # Syft emits UNKNOWN for these exact28 source-bound local modules. Preserve
    # the raw versions and reject every other missing or special-version tuple.
    verify_sbom_local_versions(sbom, binding)
    result = shared.legacy.evaluate_scans(trivy, grype, sbom, inspection, binding,
        trivy_exit_code=trivy_exit_code, grype_exit_code=grype_exit_code)
    require(result['passed'] is True and result['unresolved_count'] == 0
            and len(result['verified_dispositions']['Trivy']) == sum(observed.values())
            and len(result['verified_dispositions']['Grype']) == sum(native.values()),
            'The independent complete factual evaluation differs')
    return result


def validate_authorization(auth, request):
    require(type(auth) is dict and auth.get('accepted') is True and auth.get('actor_kind') == 'human_user_via_chat'
            and auth.get('status') == 'EXPLICIT_HUMAN_APPROVAL_RECORDED'
            and type(auth.get('authorization_source')) is str and auth['authorization_source'],
            'New exact-subject human authorization is pending')
    require(auth.get('request_sha256') == REQUEST_SHA256 and auth.get('subject') == subject_for(request['binding']),
            'Old or different-subject authorization cannot be inherited')
    require(shared.row_counter(auth['exact_target_advisory_rows'], len(request['binding']['allowed_findings']), with_disposition=True)
            == expected_counter(request['binding'], 'allowed_findings', disposition=True), 'The exact human-approved scope differs')
    policy = auth['policy']
    require(policy.get('validity') == 'change-driven' and policy.get('expires') == request['binding']['expires']
            and all(policy.get(flag) is True for flag in shared.POLICY_FLAGS), 'Exact authorization conditions differ')


def verify_published_delivery(root, request):
    from scripts import fetch_grafana_candidate as fetch
    require(type(TRANSPORT_SHA256) is str and HEX.fullmatch(TRANSPORT_SHA256)
            and type(PUBLICATION_SHA256) is str and HEX.fullmatch(PUBLICATION_SHA256),
            'Real verified publication/transport data pins are pending')
    profile = fetch.LoaderProfile(sys.modules[__name__], TRANSPORT_PATH, TRANSPORT_SHA256,
                                  RELEASE_TAG, APPROVED_TAG, SCAN_TAG)
    transport = fetch.load_transport(root, request['binding'], profile=profile)
    path = repository_file(root, PUBLICATION_PATH.as_posix())
    require(file_hash(path) == PUBLICATION_SHA256, 'Actual publication receipt bytes changed')
    publication = read_json(path)
    binding, proof = request['binding'], publication['proof']
    require(publication['published'] is True and publication['request_sha256'] == REQUEST_SHA256
            and publication['subject'] == subject_for(binding)
            and publication['actual_tag_commit'] == RELEASE_SOURCE_COMMIT
            and publication['release_tag'] == RELEASE_TAG
            and publication['release_id'] == transport['release_id']
            and publication['transport_sha256'] == TRANSPORT_SHA256
            and all(proof[k] == binding[k] for k in ('index_digest', 'platform_manifest_digest',
                'config_digest', 'archive_sha256', 'binary_sha256'))
            and proof['recomputed_diff_ids'] == binding['diff_ids'], 'Published exact-subject provenance differs')
    return {'published_subject_verified': True, 'transport_sha256': TRANSPORT_SHA256,
            'publication_sha256': PUBLICATION_SHA256, 'artifact_approved': False}


def final_result(facts, request, auth=None, *, prepare=False):
    result = {**facts, 'passed': False, 'technical_ready': True, 'approval_pending': True,
              'old_authorization_inherited': False, 'raw_reports_modified': False,
              'request_sha256': REQUEST_SHA256, 'mode': 'fixed20261107_preparation_only_never_authorizes'}
    if prepare:
        return result
    validate_authorization(auth, request)
    return {**result, 'passed': True, 'approval_pending': False, 'mode': 'fixed20261107_new_exact_subject_authorized'}


def validate_output(args):
    shared.validate_output(args, root=ROOT)


def main(argv=None):
    return shared.main(argv, consumer=sys.modules[__name__])


if __name__ == '__main__':
    raise SystemExit(main())
