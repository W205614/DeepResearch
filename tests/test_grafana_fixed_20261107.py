"""Tiny real OCI and exact c6 input boundaries; no real acceptance or publication."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'pyproject.toml').is_file())
PREP = ROOT
FIXTURES = ROOT / 'tests/fixtures/grafana-fixed-20261107'


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    if '.' in name:
        parent, field = name.rsplit('.', 1)
        setattr(__import__(parent, fromlist=[field]), field, result)
    spec.loader.exec_module(result)
    return result


build = module('scripts.grafana_release_build_identity', PREP / 'scripts/grafana_release_build_identity.py')
consumer = module('scripts.check_grafana_release_20261107', PREP / 'scripts/check_grafana_release_20261107.py')
delivery = module('scripts.grafana_release_20261107_delivery', PREP / 'scripts/grafana_release_20261107_delivery.py')
source_gate = module('scripts.check_grafana_source_fixed_20261107', PREP / 'scripts/check_grafana_source_fixed_20261107.py')
oci = module('new_release_real_oci_fixture', ROOT / 'tests/test_oci_artifact.py')


@pytest.fixture
def identity():
    return json.loads((ROOT / 'deployment/security/grafana-fixed-20261107-build-identity.json').read_bytes())


def test_actual_c6_51_inputs_pass_without_changing_any_original_bytes(identity):
    before = {n: hashlib.sha256((ROOT / n).read_bytes()).hexdigest() for n in identity['actual_build_input_sha256']}
    result = build.verify_build_identity(ROOT, identity)
    assert result['actual_input_count'] == 51 and not result['artifact_approved']
    assert result['exact_dual_identity_paths'] == ['scripts/oci_artifact.py']
    assert result['observed_input_sha256'] == before


@pytest.mark.parametrize('change', ['other_source', 'extra_normalization', 'wrong_source', 'wrong_git_blob', 'clean_filter'])
def test_dual_identity_does_not_allow_another_source_or_unreviewed_clean_rule(identity, change):
    value = copy.deepcopy(identity)
    if change == 'other_source':
        value['actual_build_input_sha256']['.dockerignore'] = '0' * 64
    elif change == 'extra_normalization':
        value['provenance']['git_text_normalizations']['.dockerignore'] = value['provenance']['git_text_normalizations'][build.TEXT_INPUT]
    elif change == 'wrong_source':
        value['source_commit'] = '0' * 40
    elif change == 'wrong_git_blob':
        value['provenance']['git_text_normalizations'][build.TEXT_INPUT]['git_blob_object_id'] = '0' * 40
    else:
        value['provenance']['git_text_normalizations'][build.TEXT_INPUT]['git_attributes']['filter'] = 'unreviewed'
    with pytest.raises(ValueError):
        build.verify_build_identity(ROOT, value)


def test_both_exact_reviewed_physical_forms_pass_but_other_byte_changes_do_not(identity, tmp_path):
    for name in identity['actual_build_input_sha256']:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / name).read_bytes())
    text = tmp_path / build.TEXT_INPUT
    canonical = text.read_bytes().replace(b'\r\n', b'\n')
    assert hashlib.sha256(canonical).hexdigest() == build.TEXT_COMMITTED_SHA256
    text.write_bytes(canonical)
    build.verify_build_identity(tmp_path, identity)
    text.write_bytes(canonical + b'\n')
    with pytest.raises(ValueError, match='bytes changed'):
        build.verify_build_identity(tmp_path, identity)


def test_actual_committed_input_bytes_survive_lf_checkout_and_default_windows_text_checkout_fails_with_guidance(identity, tmp_path):
    repo = tmp_path / 'clean-git'
    repo.mkdir()
    for name, expected in identity['committed_input_sha256'].items():
        data = subprocess.check_output(['git', 'cat-file', 'blob', 'HEAD:' + name], cwd=ROOT)
        assert hashlib.sha256(data).hexdigest() == expected
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    subprocess.run(['git', 'init'], cwd=repo, check=True, capture_output=True)
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'add', '.'], cwd=repo, check=True, capture_output=True)
    lf = tmp_path / 'exact-lf-checkout'
    lf.mkdir()
    subprocess.run(['git', '-c', 'core.autocrlf=false', 'checkout-index', '--all', '--prefix=' + lf.as_posix() + '/'],
                   cwd=repo, check=True, capture_output=True)
    assert build.verify_build_identity(lf, identity)['actual_input_count'] == 51
    crlf = tmp_path / 'default-windows-checkout'
    crlf.mkdir()
    subprocess.run(['git', '-c', 'core.autocrlf=true', 'checkout-index', '--all', '--prefix=' + crlf.as_posix() + '/'],
                   cwd=repo, check=True, capture_output=True)
    with pytest.raises(ValueError, match='clone --config core.autocrlf=false'):
        build.verify_build_identity(crlf, identity)


def test_missing_new_request_pin_fails_before_reading_any_authorization(monkeypatch):
    monkeypatch.setattr(consumer, 'REQUEST_SHA256', None)
    with pytest.raises(ValueError, match='request pin is pending'):
        consumer.load_request(ROOT)


def test_new_loader_wrapper_uses_the_real_profile_hook_and_fails_before_http_or_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(consumer, 'REQUEST_SHA256', None)
    common = module('new_release_common_fetch', PREP / 'scripts/fetch_grafana_candidate.py')
    wrapper = module('new_release_fetch_wrapper', PREP / 'scripts/fetch_grafana_fixed_20261107.py')
    monkeypatch.setattr(wrapper, 'common', common)
    monkeypatch.setattr(common, 'ROOT', tmp_path)
    monkeypatch.setattr(common, 'download_parts', lambda *_a, **_k: pytest.fail('HTTP before new authority'))
    monkeypatch.setattr(common, 'load_image', lambda *_a, **_k: pytest.fail('Docker before new authority'))
    output = tmp_path / '.cache/eval/pending-profile.json'
    assert wrapper.main(['--output', str(output)]) == 1
    assert json.loads(output.read_bytes())['passed'] is False
    profile = wrapper.loader_profile()
    assert profile.consumer is consumer and profile.approved_tag == 'deepresearch-approved-grafana:20261107'


def test_new_scan_wrapper_uses_real_consumer_and_rejects_missing_request_before_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(consumer, 'REQUEST_SHA256', None)
    common = module('new_release_common_scan', PREP / 'scripts/scan_grafana_candidate.py')
    wrapper = module('new_release_scan_wrapper', PREP / 'scripts/scan_grafana_fixed_20261107.py')
    monkeypatch.setattr(wrapper, 'common', common)
    monkeypatch.setattr(common, 'run', lambda *_a, **_k: pytest.fail('Tool operation before new authority'))
    output = tmp_path / '.cache/eval/scans'
    assert wrapper.main(['--output-dir', str(output)]) == 1
    assert not output.exists()


def test_real_isolated_subprocess_cli_works_without_pythonpath_or_editable_install(tmp_path):
    project = tmp_path / 'clean-lf-project'
    shutil.copytree(ROOT / 'scripts', project / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
    pending_consumer = project / 'scripts/check_grafana_release_20261107.py'
    source_lines = pending_consumer.read_text().splitlines()
    pending_consumer.write_bytes(('\n'.join('REQUEST_SHA256 = None' if line.startswith('REQUEST_SHA256 = ') else line
                                for line in source_lines) + '\n').encode())
    caller = tmp_path / 'unrelated-caller'
    caller.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in {'PYTHONPATH', 'PYTHONHOME', 'GITHUB_TOKEN'}}
    entries = ['check_grafana_release_20261107.py', 'fetch_grafana_fixed_20261107.py',
               'scan_grafana_fixed_20261107.py', 'publish_grafana_fixed_20261107.py',
               'check_grafana_source_fixed_20261107.py', 'bind_grafana_fixed_delivery.py']
    for entry in entries:
        command = [sys.executable, '-I', str(project / 'scripts' / entry)]
        help_result = subprocess.run(command + ['--help'], cwd=caller, env=env,
                                     capture_output=True, timeout=30)
        assert help_result.returncode == 0, help_result.stderr.decode()
        assert b'usage:' in help_result.stdout and b'ModuleNotFoundError' not in help_result.stderr
        output = project / '.cache/eval' / (entry + '.json')
        if entry.startswith('check_grafana_source_'):
            arguments = [x for flag in ['scan', 'scan-receipt', 'original-verdict', 'candidate-verdict', 'runtime-inventory']
                         for x in ['--' + flag, str(project / 'absent-input.json')]] + ['--output', str(output)]
        elif entry.startswith('check_'):
            arguments = [x for flag in ['archive', 'scan', 'grype-scan', 'sbom', 'inspect']
                         for x in ['--' + flag, str(project / 'absent-input.json')]]
            arguments += ['--output', str(output), '--scanner-exit-code', '0', '--grype-exit-code', '0']
        elif entry.startswith('fetch_'):
            arguments = ['--output', str(output)]
        elif entry.startswith('scan_'):
            arguments = ['--output-dir', str(output)]
        elif entry.startswith('publish_'):
            arguments = ['--archive', str(project / 'absent.oci.tar'), '--output-dir', str(output), '--release-id', '17']
        else:
            arguments = ['--transport', str(project / 'absent-transport.json'), '--publication',
                         str(project / 'absent-publication.json'), '--output', str(output)]
        pending = subprocess.run(command + arguments, cwd=caller, env=env,
                                 capture_output=True, timeout=30)
        assert pending.returncode == 1
        assert b'ModuleNotFoundError' not in pending.stderr
        if entry.startswith(('publish_', 'bind_')):
            assert b'new request pin is pending' in pending.stderr
            assert not output.exists()
        elif entry.startswith('scan_'):
            result = json.loads(pending.stdout)
            assert result['operational_success'] is False and not output.exists()
        else:
            assert json.loads(output.read_bytes())['passed'] is False


@pytest.fixture
def review_set(tmp_path):
    evidence = tmp_path / 'evidence.txt'
    evidence.write_bytes(b'synthetic immutable fixture evidence\n')
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    binding = {'artifact': 'grafana', 'archive_bytes': 1, 'archive_sha256': 'a' * 64,
        'index_digest': 'sha256:' + '1' * 64, 'platform_manifest_digest': 'sha256:' + '2' * 64,
        'config_digest': 'sha256:' + '3' * 64, 'diff_ids': ['sha256:' + '4' * 64],
        'binary_sha256': {'usr/share/grafana/bin/grafana': 'b' * 64}}
    rows = [{'id': 'CVE-FIXTURE-' + str(i), 'target': 'usr/share/grafana/bin/grafana', 'package': 'fixture',
             'version': 'v1.0.0', 'severity': 'HIGH'} for i in range(18)]
    binding.update(allowed_findings=[{**r, 'disposition': 'NOT_AFFECTED', 'review_disposition': 'NOT_APPLICABLE'} for r in rows],
                   exact_trivy_rows=rows, exact_grype_rows=rows[:7], scanner_exact_aliases=[])
    request = {'binding': binding, 'candidate_author': '/root/candidate-author',
               'files_sha256': {'evidence.txt': digest}, 'independent_reviews': []}
    for i in range(2):
        review = {'facts_passed': True, 'artifact_approved': False, 'human_risk_acceptance': False,
            'actor_kind': 'automated_independent_agent', 'actor': '/root/independent-' + str(i),
            'subject': consumer.subject_for(binding), 'rows': [{**r, 'disposition': 'NOT_APPLICABLE',
                'subject_elf_sha256': 'b' * 64, 'technical_basis': 'SYNTHETIC TEST ONLY',
                'evidence': [{'path': 'evidence.txt', 'sha256': digest}]} for r in rows],
            'exact_trivy_rows': rows, 'exact_grype_rows': rows[:7], 'scanner_exact_aliases': []}
        path = tmp_path / f'review-{i}.json'
        path.write_bytes((json.dumps(review) + '\n').encode())
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        request['files_sha256'][path.name] = sha
        request['independent_reviews'].append({'path': path.name, 'sha256': sha})
    return tmp_path, request


def test_each_independent_review_must_cover_exact_new_rows_and_explicit_enum_mapping(review_set):
    root, request = review_set
    assert consumer.verify_reviews(root, request) == {'/root/independent-0', '/root/independent-1'}


@pytest.mark.parametrize('change', ['author', 'old_subject', 'missing', 'duplicate', 'changed_row',
    'changed_native', 'changed_alias', 'fixed_enum', 'missing_basis', 'missing_evidence', 'evidence_bytes'])
def test_independent_row_scope_authorship_evidence_and_enum_drift_cannot_pass(review_set, change):
    root, request = review_set
    path = root / request['independent_reviews'][0]['path']
    review = json.loads(path.read_bytes())
    if change == 'author':
        review['actor'] = request['candidate_author']
    elif change == 'old_subject':
        review['subject']['config_digest'] = 'sha256:' + 'f' * 64
    elif change == 'missing':
        review['rows'].pop()
    elif change == 'duplicate':
        review['rows'][-1] = copy.deepcopy(review['rows'][0])
    elif change == 'changed_row':
        review['rows'][0]['severity'] = 'UNKNOWN'
    elif change == 'changed_native':
        review['exact_grype_rows'].pop()
    elif change == 'changed_alias':
        review['scanner_exact_aliases'] = [{'unreviewed': True}]
    elif change == 'fixed_enum':
        review['rows'][0]['disposition'] = 'FIXED'
    elif change == 'missing_basis':
        review['rows'][0]['technical_basis'] = ''
    elif change == 'missing_evidence':
        review['rows'][0]['evidence'] = []
    else:
        (root / 'evidence.txt').write_bytes(b'changed')
    path.write_bytes((json.dumps(review) + '\n').encode())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    request['independent_reviews'][0]['sha256'] = digest
    request['files_sha256'][path.name] = digest
    with pytest.raises(ValueError):
        consumer.verify_reviews(root, request)


@pytest.fixture
def source_policy():
    raw = FIXTURES / 'source-77.raw.json.gz'
    scan = json.loads(__import__('gzip').decompress(raw.read_bytes()))
    scope = {'rows': [{'id': v['VulnerabilityID'], 'target': r['Target'], 'package': v['PkgName'],
                      'version': v['InstalledVersion'], 'severity': v['Severity']}
                     for r in scan['Results'] for v in r.get('Vulnerabilities', []) if v['Severity'] == 'HIGH'],
             'scan_targets': sorted([r['Target'], r.get('Class', ''), r.get('Type', '')] for r in scan['Results']),
             'manifest_copies': json.loads((ROOT / 'deployment/security/grafana-fixed-20261107-source-scope.json').read_bytes())['manifest_copies']}
    names = ['main', *[m['plugin_directory'] for m in json.loads((ROOT / 'deployment/vendors/grafana-20261107/plugin-modules.json').read_bytes())]]
    compiled = {name: {'elf_sha256': 'a' * 64, 'packages': [], 'native': {
        'golang.org/x/net': {'Path': 'golang.org/x/net', 'Version': 'v0.62.0'},
        'github.com/go-jose/go-jose/v4': {'Path': 'github.com/go-jose/go-jose/v4', 'Version': 'v4.1.6'}}} for name in names}
    compiled['tempo']['native']['github.com/grafana/tempo'] = {'Path': 'github.com/grafana/tempo',
        'Version': 'v1.5.1-0.20260910130453-bcfe9f230c1d', 'Sum': 'h1:BphMCaYqWY+j1fdRPNFPkYE4A4QIev1KlHjRkSVJ8O4='}
    return scan, scope, compiled


def test_full_source_policy_preserves_real_twenty_two_high_seventy_seven_targets_and_unknown(source_policy):
    scan, scope, compiled = source_policy
    result = source_gate.assess_source(scan, scope, compiled)
    assert result['technical_ready'] and not result['passed'] and result['approval_pending']
    assert result['raw_blocking_count'] == 22 and result['full_source_targets'] == 77
    assert result['unknown_dispositions_granted'] == 0


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'reclassified', 'extra_high', 'secret',
    'coverage', 'bad_fixed_module', 'tempo_server'])
def test_source_row_coverage_secret_and_actual_compiled_code_drift_blocks(source_policy, change):
    scan, scope, compiled = source_policy
    record = next(r for r in scan['Results'] if any(v['Severity'] == 'HIGH' for v in r.get('Vulnerabilities', [])))
    item = next(v for v in record['Vulnerabilities'] if v['Severity'] == 'HIGH')
    if change == 'missing':
        record['Vulnerabilities'].remove(item)
    elif change == 'duplicate':
        record['Vulnerabilities'].append(copy.deepcopy(item))
    elif change == 'reclassified':
        item['Severity'] = 'UNKNOWN'
    elif change == 'extra_high':
        record['Vulnerabilities'].append({**item, 'VulnerabilityID': 'UNREVIEWED'})
    elif change == 'secret':
        record['Secrets'] = [{'RuleID': 'synthetic', 'Severity': 'LOW'}]
    elif change == 'coverage':
        scan['Results'].pop()
    elif change == 'bad_fixed_module':
        compiled['main']['native']['golang.org/x/net']['Version'] = 'v0.60.0'
    else:
        compiled['tempo']['packages'].append('github.com/grafana/tempo/modules/frontend')
    with pytest.raises(ValueError):
        source_gate.assess_source(scan, scope, compiled)


@pytest.fixture
def publishing(tmp_path, monkeypatch):
    archive, binding = oci.fixture(tmp_path, [oci.BASE])
    binding.update(artifact='grafana', allowed_findings=[], expires='change-driven')
    request = {'binding': binding}
    # Synthetic authorization exists only in memory for this tiny fixture. No
    # human-acceptance record or real release is written anywhere by these tests.
    auth = {'accepted': True, 'actor_kind': 'human_user_via_chat',
            'status': 'EXPLICIT_HUMAN_APPROVAL_RECORDED', 'authorization_source': 'SYNTHETIC TEST ONLY',
            'request_sha256': 'a' * 64, 'subject': consumer.subject_for(binding),
            'exact_target_advisory_rows': [], 'policy': {'validity': 'change-driven', 'expires': 'change-driven',
                **dict.fromkeys(consumer.shared.POLICY_FLAGS, True)}}
    monkeypatch.setattr(consumer, 'REQUEST_SHA256', 'a' * 64)
    api = SimpleNamespace(ROOT=tmp_path, AUTH_PATH=Path('never-written-human-authorization.json'),
        REQUEST_SHA256='a' * 64, MAX_ARCHIVE_BYTES=consumer.MAX_ARCHIVE_BYTES,
        RELEASE_TAG=consumer.RELEASE_TAG, RELEASE_SOURCE_COMMIT=consumer.RELEASE_SOURCE_COMMIT,
        load_request=lambda root: request, read_json=lambda path: auth,
        validate_authorization=consumer.validate_authorization, subject_for=consumer.subject_for)
    return api, archive, binding, auth


class ReleaseClient:
    def __init__(self, api, binding, *, wrong_tag=False, unrelated=False, revoke=None):
        self.api, self.binding = api, binding
        self.calls, self.assets, self.draft = [], [], True
        self.wrong_tag, self.revoke = wrong_tag, revoke
        if unrelated:
            self.assets.append({'id': 99, 'name': 'unrelated'})

    def call(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path.startswith('/git/ref/'):
            return {'object': {'type': 'commit', 'sha': 'f' * 40 if self.wrong_tag else self.api.RELEASE_SOURCE_COMMIT}}
        if path == '/releases/17/assets?per_page=100':
            return self.assets
        if path == '/releases/17':
            if kwargs.get('method') == 'PATCH':
                self.draft = False
            return {'id': 17, 'tag_name': self.api.RELEASE_TAG, 'draft': self.draft, 'prerelease': False,
                'body': self.api.REQUEST_SHA256 + self.binding['archive_sha256'],
                'target_commitish': self.api.RELEASE_SOURCE_COMMIT, 'published_at': '2026-11-07T00:00:00Z'}
        if not path and 'file' in kwargs:
            release, name, file = kwargs['file']
            assert release == 17
            data = file.read_bytes()
            asset = {'id': len(self.assets) + 1, 'name': name, 'size': len(data), 'state': 'uploaded',
                'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
                'url': f'https://api.github.com/repos/W205614/DeepResearch/releases/assets/{len(self.assets) + 1}'}
            self.assets.append(asset)
            if self.revoke is not None:
                self.revoke['accepted'] = False
            return asset
        raise AssertionError('Unexpected external operation: ' + path)


def test_prepare_uses_real_oci_but_never_returns_authorized_or_calls_network(publishing):
    api, archive, _, _ = publishing
    result = delivery.prepare(api, archive)
    assert result['technical_ready'] and not result['passed'] and result['approval_pending']
    assert result['network_or_Docker_operations'] is False and result['authorization_granted'] is False


@pytest.mark.parametrize('change', ['pending', 'old_request', 'another_subject', 'row_scope', 'nonhuman'])
def test_pending_or_inherited_acceptance_prevents_every_publication_operation(publishing, change):
    api, archive, binding, auth = publishing
    if change == 'pending':
        auth['accepted'] = False
    elif change == 'old_request':
        auth['request_sha256'] = '4e06f257a627bbbecaf3f2c3d867b8f0edbe5be7e32c317660b1296a1d8c7758'
    elif change == 'another_subject':
        auth['subject'] = {**auth['subject'], 'config_digest': 'sha256:' + '0' * 64}
    elif change == 'row_scope':
        auth['exact_target_advisory_rows'] = [{'id': 'UNREVIEWED'}]
    else:
        auth['actor_kind'] = 'automated_agent'
    client = ReleaseClient(api, binding)
    output = api.ROOT / '.cache/eval/publish'
    with pytest.raises(ValueError):
        delivery.publish(api, client, archive, output, release_id=17)
    assert client.calls == [] and not output.exists()


def test_tiny_real_oci_happy_path_generates_only_observed_delivery_ids_and_physical_lf(publishing):
    api, archive, binding, _ = publishing
    client = ReleaseClient(api, binding)
    output = api.ROOT / '.cache/eval/publish'
    transport, publication = delivery.publish(api, client, archive, output, release_id=17)
    assert publication['published'] and transport['release_id'] == 17
    assert publication['subject'] == consumer.subject_for(binding)
    assert publication['proof']['authorization_evaluated'] is False
    assert all(b'\r\n' not in p.read_bytes() for p in output.glob('*.json'))
    assert (output / 'transport.json').read_bytes() == (json.dumps(transport, indent=2) + '\n').encode()


def test_actual_loader_download_path_matches_future_workflow_gate_and_grafana_scan_tag(publishing, monkeypatch, tmp_path):
    api, archive, binding, _ = publishing
    common = module('release_wiring_fetch', PREP / 'scripts/fetch_grafana_candidate.py')
    monkeypatch.setattr(common, 'ROOT', api.ROOT)
    api.repository_file = lambda root, name: root / name
    profile = common.LoaderProfile(api, Path('unused-transport.json'), 'c' * 64,
        api.RELEASE_TAG, 'deepresearch-approved-grafana:20261107', 'deepresearch-scan-grafana:20261107')
    monkeypatch.setattr(common, 'load_transport', lambda *_a, **_k: {'fixture': True})
    observed = []

    def download(destination, *_args, **_kwargs):
        observed.append(destination)
        return archive, {'published_verified_live': True}

    monkeypatch.setattr(common, 'download_parts', download)
    monkeypatch.setattr(common, 'verify_publication', lambda *_a, **_k: {'published_verified_live': True})
    monkeypatch.setattr(common, 'load_image', lambda *_a, **_k: {'Id': binding['config_digest']})
    output = api.ROOT / '.cache/eval/loader-wiring.json'
    assert common.main(['--tag', profile.scan_tag, '--output', str(output)], profile_override=profile,
        profile_name='fixed20261107', cache_archive_name='approved-grafana-fixed-20261107.oci.tar') == 0
    assert observed == [api.ROOT / '.cache/eval/approved-grafana-fixed-20261107.oci.tar']
    # Apply the pre-rendered selector patch only to a tiny temporary repository.
    workspace = tmp_path / 'future-selectors'
    for name in ['compose.yaml', 'scripts/runtime_ci_inventory.py', '.github/workflows/ci.yml', '.github/workflows/security.yml']:
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((FIXTURES / 'selectors-before' / name).read_bytes())
    subprocess.run(['git', 'init'], cwd=workspace, check=True, capture_output=True)
    subprocess.run(['git', 'apply', str(FIXTURES / 'activation.patch')], cwd=workspace, check=True, capture_output=True)
    import yaml
    workflow = yaml.safe_load((workspace / '.github/workflows/security.yml').read_text())
    steps = workflow['jobs']['runtime-image-scan']['steps']
    approved = next(s for s in steps if s.get('id') == 'approved_scan')
    assert approved['with']['image-ref'] == "${{ matrix.artifact == 'grafana' && 'deepresearch-scan-grafana:20261107' || 'deepresearch-scan-minio:ci' }}"
    load = next(s['run'] for s in steps if 'fetch_grafana_fixed_20261107.py' in s.get('run', ''))
    scan = next(s['run'] for s in steps if 'scan_grafana_fixed_20261107.py' in s.get('run', ''))
    gate = next(s['run'] for s in steps if 'check_grafana_release_20261107.py' in s.get('run', ''))
    assert '--tag ' + profile.scan_tag in load and '--image ' + profile.scan_tag in scan
    assert '--archive .cache/eval/' + observed[0].name in gate
    assert 'SCAN_TAG: deepresearch-scan-${{ matrix.artifact }}:ci' in (workspace / '.github/workflows/security.yml').read_text()


@pytest.mark.parametrize('change', ['wrong_tag', 'unrelated', 'revoke_before_publish', 'unreal_release_id'])
def test_source_asset_or_late_authority_drift_blocks_publication(publishing, change):
    api, archive, binding, auth = publishing
    client = ReleaseClient(api, binding, wrong_tag=change == 'wrong_tag', unrelated=change == 'unrelated',
                           revoke=auth if change == 'revoke_before_publish' else None)
    output = api.ROOT / '.cache/eval/publish'
    with pytest.raises(ValueError):
        delivery.publish(api, client, archive, output, release_id=0 if change == 'unreal_release_id' else 17)
    assert not any(kwargs.get('method') == 'PATCH' for _, kwargs in client.calls)


@pytest.fixture
def actual_full_reports(monkeypatch):
    request = json.loads((ROOT / 'deployment/security/grafana-fixed-20261107-request.json').read_bytes())
    evidence = ROOT / 'deployment/security/grafana-fixed-20261107-evidence'
    trivy, grype, sbom = [json.loads((evidence / 'scans' / ('full-' + tool + '.json')).read_bytes())
                         for tool in ['trivy', 'grype', 'syft']]
    inspection = json.loads((evidence / 'image-inspect.json').read_bytes())
    from datetime import datetime
    timestamp = max(datetime.fromisoformat(trivy['CreatedAt'].replace('Z', '+00:00')),
                    datetime.fromisoformat(grype['descriptor']['timestamp'].replace('Z', '+00:00')))

    class RecordedScanClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return timestamp.astimezone(tz) if tz else timestamp.replace(tzinfo=None)

    # Pin the external clock to the retained scan event; raw timestamps and all
    # scanner evidence stay byte-for-byte unchanged across future CI runs.
    monkeypatch.setattr(consumer, 'datetime', RecordedScanClock)
    return request, trivy, grype, sbom, inspection


def test_actual_final_full_scans_pass_facts_and_keep_preparation_unapproved(actual_full_reports):
    request, trivy, grype, sbom, inspection = actual_full_reports
    facts = consumer.verify_full_scans(trivy, grype, sbom, inspection, request['binding'],
                                       trivy_exit_code=0, grype_exit_code=0)
    assert facts['passed'] and facts['unresolved_count'] == 0
    result = consumer.final_result(facts, request, prepare=True)
    assert result['technical_ready'] and not result['passed'] and result['approval_pending']
    assert len([a for a in sbom['artifacts'] if a['version'] == 'UNKNOWN']) == 28


@pytest.mark.parametrize('change', ['new_unknown_finding', 'duplicate_finding', 'low_secret',
    'extra_uppercase_unknown', 'wrong_local_target', 'unknown_case', 'devel_version',
    'absent_version', 'non_go_local', 'duplicate_local', 'wrong_local_elf'])
def test_actual_final_full_scans_reject_unreviewed_findings_and_local_version_drift(actual_full_reports, change):
    request, trivy, grype, sbom, inspection = actual_full_reports
    local = next(a for a in sbom['artifacts'] if a['version'] == 'UNKNOWN')
    if change in {'new_unknown_finding', 'duplicate_finding'}:
        target = next(r for r in trivy['Results'] if r.get('Vulnerabilities'))
        row = copy.deepcopy(target['Vulnerabilities'][0])
        if change == 'new_unknown_finding':
            row['VulnerabilityID'], row['Severity'] = 'GO-unreviewed-new', 'UNKNOWN'
        target['Vulnerabilities'].append(row)
    elif change == 'low_secret':
        trivy['Results'][0]['Secrets'] = [{'RuleID': 'unreviewed', 'Severity': 'LOW', 'Match': 'synthetic-not-a-key'}]
    elif change == 'extra_uppercase_unknown':
        next(a for a in sbom['artifacts'] if a['version'] != 'UNKNOWN')['version'] = 'UNKNOWN'
    elif change == 'wrong_local_target':
        local['locations'][0]['path'] = '/usr/share/grafana/bin/unreviewed'
    elif change == 'unknown_case':
        local['version'] = 'unknown'
    elif change == 'devel_version':
        local['version'] = '(devel)'
    elif change == 'absent_version':
        local['version'] = None
    elif change == 'non_go_local':
        local['type'] = 'binary'
        next(a for a in sbom['artifacts'] if a['type'] == 'binary' and a is not local)['type'] = 'go-module'
    elif change == 'duplicate_local':
        sbom['artifacts'].append(copy.deepcopy(local))
    elif change == 'wrong_local_elf':
        request['binding']['inventory_identity']['local_source_modules'][0]['elf_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        consumer.verify_full_scans(trivy, grype, sbom, inspection, request['binding'],
                                   trivy_exit_code=0, grype_exit_code=0)


@pytest.fixture
def actual_compiled_source_scope():
    request = json.loads((ROOT / 'deployment/security/grafana-fixed-20261107-request.json').read_bytes())
    scope = json.loads((ROOT / 'deployment/security/grafana-fixed-20261107-source-scope.json').read_bytes())
    return request, scope


def test_actual_complete_profiles_bind_all_fifteen_visible_source_snapshot_manifests(actual_compiled_source_scope):
    request, scope = actual_compiled_source_scope
    facts = source_gate.verify_profiles(ROOT, request, scope)
    assert len(facts) == 14 and len(scope['manifest_copies']) == 15
    assert facts['main']['package_count'] == 4200 and facts['tempo']['package_count'] == 605
    scan = json.loads(__import__('gzip').decompress((FIXTURES / 'source-77.raw.json.gz').read_bytes()))
    result = source_gate.assess_source(scan, scope, facts)
    assert result['raw_blocking_count'] == 22 and result['full_source_targets'] == 77
    assert not result['passed'] and result['unknown_dispositions_granted'] == 0


@pytest.mark.parametrize('change', ['missing_copy', 'duplicate_copy', 'wrong_sha', 'wrong_profile',
                                    'wrong_canonical_source', 'wrong_module'])
def test_source_snapshot_copy_mapping_cannot_escape_its_compiled_profile(actual_compiled_source_scope, change):
    request, scope = actual_compiled_source_scope
    row = scope['manifest_copies'][0]
    if change == 'missing_copy':
        scope['manifest_copies'].pop()
    elif change == 'duplicate_copy':
        scope['manifest_copies'][-1] = copy.deepcopy(row)
    elif change == 'wrong_sha':
        row['sha256'] = '0' * 64
    elif change == 'wrong_profile':
        row['compiled_profile'] = 'unreviewed'
    elif change == 'wrong_canonical_source':
        row['compiled_source'] = 'deployment/vendors/grafana/main/go.mod'
    else:
        row['module_directive'] = 'module unreviewed'
    with pytest.raises(ValueError):
        source_gate.verify_profiles(ROOT, request, scope)
