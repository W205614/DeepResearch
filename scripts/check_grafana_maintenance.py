"""Verify the newly reviewed maintenance subject without inheriting old approval.

The original exact28 contract stays the default for its existing consumers.
This consumer binds all 28 Trivy and 12 Grype rows, including five separately
reviewed HIGH HTML rows; a severity change is never an alias to UNKNOWN.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_candidate_exact28 as shared  # noqa: E402

REQUEST_PATH = Path('deployment/security/grafana-maintenance-request.json')
AUTH_PATH = Path('deployment/security/grafana-maintenance-authorization.json')
REQUEST_SHA256 = '4e06f257a627bbbecaf3f2c3d867b8f0edbe5be7e32c317660b1296a1d8c7758'
TRANSPORT_PATH = Path('deployment/security/grafana-maintenance-transport.json')
TRANSPORT_SHA256 = None
RELEASE_TAG = 'artifact-grafana-maintenance-20261105'
APPROVED_TAG = 'deepresearch-approved-grafana:20261105'
SCAN_TAG = 'deepresearch-scan-grafana:ci'
REVIEW_PATHS = frozenset('deployment/security/grafana-maintenance-evidence/' + name for name in (
    'core-independent-review.json', 'plugins-independent-review.json'))
COPIED_SOURCE_DIRS = ('deployment/vendors/grafana/main', 'deployment/vendors/grafana-security/main')

# Shared helpers have no authority lookup or disposition side effects.
MAX_ARCHIVE_BYTES = shared.MAX_ARCHIVE_BYTES
HEX, DIGEST, POLICY_FLAGS = shared.HEX, shared.DIGEST, shared.POLICY_FLAGS
require, read_json, strict_json = shared.require, shared.read_json, shared.strict_json
file_hash, repository_file, subject_for = shared.file_hash, shared.repository_file, shared.subject_for
legacy = shared.legacy


def validate_contract(binding):
    shared.validate_contract(binding, profile='maintenance')
    require(binding.get('validity') == 'change-driven', 'Maintenance review validity changed')
    require(binding['expected_native_application'].get('version') == '13.2.5',
            'Maintenance native application differs')


def load_request(root=ROOT, now=None):
    del now  # Calendar expiry is not a substitute for the exact change-driven review.
    require(type(REQUEST_SHA256) is str and HEX.fullmatch(REQUEST_SHA256),
            'Final independent maintenance request SHA256 pin is not set')
    path = repository_file(root, REQUEST_PATH.as_posix())
    require(file_hash(path) == REQUEST_SHA256, 'Maintenance request bytes changed')
    request = read_json(path)
    require(request.get('schema') == 'deepresearch-grafana-candidate-request-v1'
            and request.get('technical_ready') is True and request.get('validity') == 'change-driven',
            'New maintenance technical request is incomplete')
    binding, files = request['binding'], request['files_sha256']
    require(request['expires'] == binding['expires'], 'Maintenance request policy differs')
    require(type(files) is dict and files and REQUEST_PATH.as_posix() not in files
            and AUTH_PATH.as_posix() not in files, 'Evidence binding is empty or circular')
    for name, digest in files.items():
        require(type(digest) is str and HEX.fullmatch(digest)
                and file_hash(repository_file(root, name)) == digest,
                'New source, recipe, raw evidence or independent review bytes changed')
    shared.validate_copied_source_inventory(root, binding, copied_dirs=COPIED_SOURCE_DIRS)
    reviews = request['independent_reviews']
    require(type(reviews) is list and len(reviews) == 2
            and {row['path'] for row in reviews} == REVIEW_PATHS,
            'Both independently bound maintenance reviews are required')
    actors = set()
    for item in reviews:
        require(files.get(item['path']) == item['sha256'], 'Independent review is outside the binding')
        review = read_json(repository_file(root, item['path']))
        require(review.get('facts_passed') is True and review.get('artifact_approved') is False
                and review.get('human_risk_acceptance') is False
                and review.get('actor_kind') == 'automated_independent_agent'
                and type(review.get('actor')) is str and review['actor'].startswith('/root/')
                and review['actor'] not in actors
                and all(review.get('subject', {}).get(key) == value
                        for key, value in subject_for(binding).items()),
                'Independent review failed, repeats an actor or binds another exact subject')
        actors.add(review['actor'])
    for name, digest in (binding['evidence_files'] | binding['recipe_files']).items():
        require(files.get(name) == digest, 'Factual helper input is outside the new file binding')
    for row in binding['inventory_identity']['local_source_modules']:
        require(row['source_go_mod'] in files and row['replace_file'] in files,
                'Local source identity proof is outside the new file binding')
    validate_contract(binding)
    legacy.validate_local_sources(binding, root)
    return request


def fresh_timestamp(value, maximum_age, *, now):
    require(type(value) is str, 'Fresh scan/database timestamp is missing')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(stamp.utcoffset() is not None
            and -timedelta(minutes=5) <= now - stamp <= maximum_age,
            'Scan or vulnerability database is stale or in the future')


def verify_full_scans(trivy, grype, sbom, inspection, binding, *, trivy_exit_code, grype_exit_code,
                      now=None):
    now = datetime.now(timezone.utc) if now is None else now
    fresh_timestamp(trivy.get('CreatedAt'), timedelta(hours=24), now=now)
    descriptor = grype.get('descriptor', {})
    require(descriptor.get('name') == 'grype' and descriptor.get('version') == '0.121.0',
            'Pinned native scanner identity differs')
    fresh_timestamp(descriptor.get('timestamp'), timedelta(hours=24), now=now)
    fresh_timestamp(descriptor.get('db', {}).get('status', {}).get('built'), timedelta(days=7), now=now)
    return shared.verify_full_scans(trivy, grype, sbom, inspection, binding,
        trivy_exit_code=trivy_exit_code, grype_exit_code=grype_exit_code, profile='maintenance')


def validate_authorization(auth, request):
    binding = request['binding']
    require(type(auth) is dict and auth.get('accepted') is True,
            'New maintenance exact-subject human authorization is pending')
    require(auth.get('request_sha256') == REQUEST_SHA256 and auth.get('subject') == subject_for(binding),
            'Maintenance authorization binds another request or subject')
    require(shared.row_counter(auth['exact_target_advisory_rows'], 33, with_disposition=True)
            == shared.row_counter(binding['allowed_findings'], 33, with_disposition=True),
            'Maintenance authorization exact per-severity scope differs')
    policy = auth['policy']
    require(type(policy) is dict and policy.get('validity') == 'change-driven'
            and policy.get('expires') == binding['expires']
            and all(policy.get(flag) is True for flag in POLICY_FLAGS),
            'Maintenance authorization security conditions differ')


def final_result(facts, request, auth=None, *, prepare=False):
    result = {**facts, 'passed': False, 'technical_ready': True, 'approval_pending': True,
              'old_authorization_inherited': False, 'raw_reports_modified': False,
              'request_sha256': REQUEST_SHA256,
              'mode': 'maintenance_preparation_only_never_authorizes'}
    if prepare:
        return result
    validate_authorization(auth, request)
    return {**result, 'passed': True, 'approval_pending': False,
            'mode': 'new_exact_subject_authorized_Grafana_maintenance'}


def validate_output(args):
    shared.validate_output(args, root=ROOT)


def main(argv=None):
    return shared.main(argv, consumer=sys.modules[__name__])


if __name__ == '__main__':
    raise SystemExit(main())
