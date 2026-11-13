from copy import deepcopy
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import shutil

import pytest

from scripts.check_codeql_results import REVIEW, blocking_findings, finding_identity, reviewed_findings

RULE = {'id': 'java/ssrf', 'properties': {'security-severity': '9.1', 'tags': ['security']}}


def sarif(extension=True, severity='9.1'):
    rule = deepcopy(RULE)
    rule['properties']['security-severity'] = severity
    result = {'ruleId': rule['id']}
    tool = {'driver': {'name': 'CodeQL', 'rules': [rule]}}
    if extension:
        tool = {'driver': {'name': 'CodeQL'}, 'extensions': [{'name': 'codeql/java-queries', 'rules': [rule]}]}
        result['rule'] = {'id': rule['id'], 'index': 0, 'toolComponent': {'index': 0}}
    return {'version': '2.1.0', 'runs': [{'tool': tool, 'results': [result]}]}


def save(tmp_path, document):
    (tmp_path / 'results.sarif').write_text(json.dumps(document), encoding='utf-8')
    return tmp_path


@pytest.mark.parametrize('extension', [False, True])
@pytest.mark.parametrize('severity,expected', [('6.9', 0), ('7.0', 1), ('9.1', 1), ('10', 1)])
def test_gate_reads_actual_rule_component_and_keeps_original_threshold(tmp_path, extension, severity, expected):
    findings = blocking_findings(save(tmp_path, sarif(extension, severity)))
    assert len(findings) == expected
    if findings:
        assert findings[0]['rule_id'] == 'java/ssrf'


def test_extension_index_does_not_resolve_against_driver_or_other_query_pack(tmp_path):
    document = sarif()
    run = document['runs'][0]
    run['tool']['driver']['rules'] = [{'id': 'java/ssrf', 'properties': {'security-severity': '1', 'tags': ['security']}}]
    run['tool']['extensions'].insert(0, {'name': 'other-pack', 'rules': deepcopy(run['tool']['driver']['rules'])})
    run['results'][0]['rule']['toolComponent']['index'] = 1
    assert len(blocking_findings(save(tmp_path, document))) == 1


@pytest.mark.parametrize('change', ['component', 'index', 'id', 'missing-rule', 'missing-score', 'nan-score',
                                  'missing-properties', 'missing-tags', 'bad-tags',
                                  'failed-invocation', 'missing-runs', 'missing-results'])
def test_incomplete_or_inconsistent_scan_fails_instead_of_counting_zero(tmp_path, change):
    document = sarif()
    run = document['runs'][0]
    if change == 'component':
        run['results'][0]['rule']['toolComponent']['index'] = 9
    elif change == 'index':
        run['results'][0]['rule']['index'] = -1
    elif change == 'id':
        run['results'][0]['rule']['id'] = 'other-rule'
    elif change == 'missing-rule':
        run['tool']['extensions'][0]['rules'] = []
    elif change == 'missing-score':
        del run['tool']['extensions'][0]['rules'][0]['properties']['security-severity']
    elif change == 'nan-score':
        run['tool']['extensions'][0]['rules'][0]['properties']['security-severity'] = 'NaN'
    elif change == 'missing-properties':
        del run['tool']['extensions'][0]['rules'][0]['properties']
    elif change == 'missing-tags':
        del run['tool']['extensions'][0]['rules'][0]['properties']['tags']
    elif change == 'bad-tags':
        run['tool']['extensions'][0]['rules'][0]['properties']['tags'] = 'security'
    elif change == 'failed-invocation':
        run['invocations'] = [{'executionSuccessful': False}]
    elif change == 'missing-runs':
        document['runs'] = []
    else:
        del run['results']
    with pytest.raises((ValueError, KeyError)):
        blocking_findings(save(tmp_path, document))


def test_empty_directory_is_not_a_pass(tmp_path):
    with pytest.raises(ValueError, match='SARIF missing'):
        blocking_findings(tmp_path)


def test_cli_fails_on_extension_security_finding_and_accepts_clean_scan(tmp_path):
    review = tmp_path / 'empty-review.json'
    review.write_text(json.dumps({'schema': 'codeql-exact-reviewed-findings-v1', 'findings': []}), encoding='utf-8')
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/check_codeql_results.py'), str(tmp_path),
               '--reviewed-findings', str(review)]
    document = sarif()
    document['runs'][0]['results'][0].update(message={'text': 'Potential SSRF'}, locations=[{'physicalLocation': {
        'artifactLocation': {'uri': 'source.java'},
        'region': {'startLine': 3, 'startColumn': 4, 'endColumn': 9}}}])
    save(tmp_path, document)
    blocked = subprocess.run(command, capture_output=True, text=True)
    assert blocked.returncode == 1 and 'security findings: 1' in blocked.stdout
    clean = sarif()
    clean['runs'][0]['results'] = []
    save(tmp_path, clean)
    passed = subprocess.run(command, capture_output=True, text=True)
    assert passed.returncode == 0 and 'security findings: 0' in passed.stdout


def reviewed_example(tmp_path):
    document = sarif()
    result = document['runs'][0]['results'][0]
    result.update(message={'text': 'Potential SSRF'}, locations=[{'physicalLocation': {
        'artifactLocation': {'uri': 'source.java'},
        'region': {'startLine': 3, 'startColumn': 4, 'endColumn': 9}}}])
    findings = blocking_findings(save(tmp_path, document))
    (tmp_path / 'source.java').write_text('exact reviewed source', encoding='utf-8')
    (tmp_path / 'proof.java').write_text('exact regression test', encoding='utf-8')
    record = {'schema': 'codeql-exact-reviewed-findings-v1', 'findings': [{
        'reason': 'false positive', 'justification': 'Fixed origin verified against a second real HTTP server',
        'identity': finding_identity(findings[0]),
        'evidence_sha256': {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
                            for name in ['source.java', 'proof.java']}}]}
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(record), encoding='utf-8')
    return findings, path, record


def test_review_accounts_only_for_exact_result_and_preserves_raw_scan(tmp_path):
    findings, path, _ = reviewed_example(tmp_path)
    raw_before = (tmp_path / 'results.sarif').read_bytes()
    additional = deepcopy(findings[0])
    additional['result']['locations'][0]['physicalLocation']['region']['startLine'] = 4
    reviewed, unresolved = reviewed_findings([*findings, additional], path, tmp_path)
    assert reviewed == findings and unresolved == [additional]
    assert (tmp_path / 'results.sarif').read_bytes() == raw_before


@pytest.mark.parametrize('change', ['source', 'proof', 'risk-acceptance', 'no-proof', 'duplicate'])
def test_review_rejects_changed_source_proof_or_unjustified_exception(tmp_path, change):
    findings, path, record = reviewed_example(tmp_path)
    if change in {'source', 'proof'}:
        (tmp_path / (change + '.java')).write_text('changed bytes', encoding='utf-8')
    elif change == 'risk-acceptance':
        record['findings'][0]['reason'] = "won't fix"
    elif change == 'no-proof':
        del record['findings'][0]['evidence_sha256']['proof.java']
    else:
        record['findings'].append(deepcopy(record['findings'][0]))
    path.write_text(json.dumps(record), encoding='utf-8')
    with pytest.raises(ValueError):
        reviewed_findings(findings, path, tmp_path)


def test_current_continuation_preserves_original_exact_identities_and_bound_bytes():
    root = Path(__file__).resolve().parents[1]
    prior_path = root / 'deployment/security/codeql-reviewed-findings-20261110.json'
    prior = json.loads(prior_path.read_bytes())
    current = json.loads(REVIEW.read_bytes())
    assert REVIEW.name == 'codeql-reviewed-findings-20261113-current-native-gates.json'
    assert [row['identity'] for row in current['findings']] == [row['identity'] for row in prior['findings']]
    assert current['findings'][:2] == prior['findings'][:2]
    for name, expected in prior['findings'][2]['evidence_sha256'].items():
        if name != 'business-backend/pom.xml':
            assert current['findings'][2]['evidence_sha256'][name] == expected
    proof = root / current['continuation_review_20261112']['evidence']
    reviewed = json.loads(proof.read_bytes())
    assert reviewed['prior_review']['sha256'] == hashlib.sha256(prior_path.read_bytes()).hexdigest()
    assert reviewed['compose_delta']['all_other_parsed_configuration_equal'] is True
    assert reviewed['html_boundary']['mtimelink_nodes'] == 0
    addendum = current['minio_module_info_addendum']
    previous = root / addendum['prior_profile']
    assert hashlib.sha256(previous.read_bytes()).hexdigest() == addendum['prior_profile_sha256']
    audit_path = root / addendum['path']
    assert hashlib.sha256(audit_path.read_bytes()).hexdigest() == addendum['sha256']
    audit = json.loads(audit_path.read_bytes())
    assert audit['recipe_delta']['all_copy_add_instructions_identical'] is True
    assert len(audit['recipe_delta']['only_added_lines']) == 5
    assert audit['new_codeql_scan_claimed'] is False
    tomcat = current['tomcat_patch_addendum']
    tomcat_path = root / tomcat['path']
    assert hashlib.sha256(tomcat_path.read_bytes()).hexdigest() == tomcat['sha256']
    assert hashlib.sha256((root / tomcat['prior_profile']).read_bytes()).hexdigest() == tomcat['prior_profile_sha256']
    renewed = json.loads(tomcat_path.read_bytes())
    assert renewed['pom_delta']['only_managed_tomcat_patch_version_changed'] is True
    assert renewed['actual_java_regression']['total'] == {'tests': 69, 'failures': 0, 'errors': 0, 'skipped': 0}
    assert renewed['new_codeql_scan_claimed'] is False
    stable = current['grafana_stable_graphs_addendum']
    stable_path = root / stable['path']
    assert hashlib.sha256(stable_path.read_bytes()).hexdigest() == stable['sha256']
    previous_profile = root / stable['prior_profile']
    assert hashlib.sha256(previous_profile.read_bytes()).hexdigest() == stable['prior_profile_sha256']
    assert current['findings'][:3] == json.loads(previous_profile.read_bytes())['findings'][:3]
    producer = json.loads(stable_path.read_bytes())
    assert producer['recipe_delta']['source_runtime_stage_bytes_identical'] is True
    assert producer['recipe_delta']['all_previous_copy_add_instructions_preserved_in_order'] is True
    assert producer['recipe_delta']['new_copy_instructions'] == [
        'COPY deployment/vendors/grafana-20261110/prepare_stable_graphs_20261112.py /tools/',
        'COPY deployment/vendors/grafana-20261110/prepare_stable_graphs_20261112.py /tools/',
        'COPY --from=plugins /external-evidence/ /plugins/',
        'COPY --from=build /external-evidence/ /main/']
    assert producer['severity_threshold_unchanged'] == 7.0
    assert producer['new_codeql_scan_claimed'] is False
    directory = current['grafana_license_directory_addendum']
    directory_path = root / directory['path']
    assert hashlib.sha256(directory_path.read_bytes()).hexdigest() == directory['sha256']
    previous_directory_profile = root / directory['prior_profile']
    assert hashlib.sha256(previous_directory_profile.read_bytes()).hexdigest() == directory['prior_profile_sha256']
    assert current['findings'][:3] == json.loads(previous_directory_profile.read_bytes())['findings'][:3]
    permission = json.loads(directory_path.read_bytes())
    assert permission['recipe_delta']['only_added_line'] == 'RUN chmod 0755 /usr/share/deepresearch/grafana-candidate/go-stdlib'
    assert permission['recipe_delta']['recursive'] is False
    assert permission['recipe_delta']['all_copy_add_instructions_identical'] is True
    assert permission['recipe_delta']['license_file_copy_mode_unchanged'] == '0644'
    assert permission['new_codeql_scan_claimed'] is False
    assert permission['actual_prior_sarif_reused']['source_sha'] == '0b532b1c277e572896ba2c51a3ec6814120e4899'
    assert permission['severity_threshold_unchanged'] == 7.0
    backend = current['backend_busybox_addendum']
    backend_path = root / backend['path']
    assert hashlib.sha256(backend_path.read_bytes()).hexdigest() == backend['sha256']
    assert hashlib.sha256((root / backend['prior_profile']).read_bytes()).hexdigest() == backend['prior_profile_sha256']
    backend_review = json.loads(backend_path.read_bytes())
    assert backend_review['compose_delta']['only_backend_build_context_and_dockerfile_changed'] is True
    assert backend_review['compose_delta']['all_other_parsed_configuration_equal'] is True
    assert backend_review['backend_boundary']['no_blanket_root_COPY_or_ADD'] is True
    assert backend_review['backend_boundary']['source_html_outside_all_host_copy_roots'] is True
    assert backend_review['severity_threshold_unchanged'] == 7.0
    assert backend_review['same_four_exact_identities'] is True
    assert backend_review['new_codeql_scan_claimed'] is False
    native = current['native_defaults_addendum']
    native_path = root / native['path']
    assert hashlib.sha256(native_path.read_bytes()).hexdigest() == native['sha256']
    assert hashlib.sha256((root / native['prior_profile']).read_bytes()).hexdigest() == native['prior_profile_sha256']
    native_review = json.loads(native_path.read_bytes())
    assert native_review['compose_delta']['only_minio_image_and_profile_selection_changed'] is True
    assert native_review['compose_delta']['all_other_parsed_configuration_equal'] is True
    assert native_review['minio_compilation_source']['source_input_count'] == 24
    assert native_review['minio_compilation_source']['all_inputs_match_actual_preparation_build'] is True
    assert native_review['minio_compilation_source']['no_blanket_host_root_COPY_or_ADD'] is True
    assert native_review['minio_compilation_source']['final_clean_source_commit_or_binary_approval_claimed'] is False
    assert native_review['cloud_backend_boundary']['actual_merged_configuration_checked'] is True
    assert native_review['cloud_backend_boundary']['deployment_claimed'] is False
    assert native_review['default_routing']['prepare_flag_or_old_minio_go_consumer_used_by_default'] is False
    assert native_review['severity_threshold_unchanged'] == 7.0
    assert native_review['same_four_exact_identities'] is True and native_review['new_findings_exempted'] is False
    assert native_review['new_codeql_scan_claimed'] is False
    timeout = current['search_timeout_defaults_addendum']
    assert hashlib.sha256((root / timeout['prior_profile']).read_bytes()).hexdigest() == timeout['prior_profile_sha256']
    timeout_path = root / timeout['path']
    assert hashlib.sha256(timeout_path.read_bytes()).hexdigest() == timeout['sha256']
    timeout_review = json.loads(timeout_path.read_bytes())
    assert timeout_review['all_other_parsed_compose_fields_equal'] is True
    assert set(timeout_review['default_deltas']) == {'compose.yaml', '.env.example'}
    assert all(row['only_two_timeout_default_numbers_changed'] for row in timeout_review['default_deltas'].values())
    assert timeout_review['severity_threshold_unchanged'] == 7.0
    assert timeout_review['same_four_exact_identities'] is True
    assert timeout_review['new_codeql_scan_claimed'] is False
    native_gates = current['current_native_gates_addendum']
    previous_native_gates = root / native_gates['prior_profile']
    assert hashlib.sha256(previous_native_gates.read_bytes()).hexdigest() == native_gates['prior_profile_sha256']
    native_gates_path = root / native_gates['path']
    assert hashlib.sha256(native_gates_path.read_bytes()).hexdigest() == native_gates['sha256']
    gate_review = json.loads(native_gates_path.read_bytes())
    assert set(gate_review['exact_bound_code_deltas']) == {
        'scripts/build_go_stdlib_security_20261112.py',
        'scripts/check_agent_native_packages.py', '.github/workflows/security.yml'}
    assert current['findings'][:3] == json.loads(previous_native_gates.read_bytes())['findings'][:3]
    assert gate_review['severity_threshold_unchanged'] == 7.0
    assert gate_review['same_four_exact_identities'] is True
    assert gate_review['new_findings_exempted'] is False
    assert gate_review['new_codeql_scan_claimed'] is False
    assert gate_review['raw_sarif_modified'] is False
    assert reviewed_findings([], REVIEW, root) == ([], [])


@pytest.mark.parametrize('path', [
    'scripts/check_agent_native_packages.py',
    'scripts/check_keycloak_zlib_20261113.py',
    'deployment/security/grafana-source-20261112-evidence/source-python-scope-20261113.json',
    'deployment/security/codeql-current-native-gates-addendum-20261113.json',
])
def test_current_continuation_rejects_changed_native_gates_or_scope_proof(tmp_path, path):
    root = Path(__file__).resolve().parents[1]
    record = json.loads(REVIEW.read_bytes())
    for row in record['findings']:
        for name in row['evidence_sha256']:
            target = tmp_path / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, target)
    review_path = tmp_path / 'review.json'
    review_path.write_bytes(REVIEW.read_bytes())
    assert reviewed_findings([], review_path, tmp_path) == ([], [])
    changed = tmp_path / path
    changed.write_bytes(changed.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='reviewed source or proof changed'):
        reviewed_findings([], review_path, tmp_path)


@pytest.mark.parametrize('change', ['web-context', 'new-go-copy', 'proof', 'addendum', 'pom', 'tomcat-addendum',
                                    'grafana-recipe', 'stable-helper', 'original-capture', 'plugin-inventory', 'stable-addendum',
                                    'license-mode', 'license-file', 'license-addendum',
                                    'backend-recipe', 'backend-benign', 'backend-ignore', 'backend-addendum',
                                    'minio-native-recipe', 'minio-source-lock', 'minio-collector', 'minio-default-local',
                                    'minio-default-inventory', 'minio-workflow', 'cloud-build-overlay',
                                    'cloud-preflight', 'native-defaults-addendum', 'timeout-template', 'timeout-addendum'])
def test_current_continuation_still_rejects_changed_compose_go_recipe_or_proof(tmp_path, change):
    root = Path(__file__).resolve().parents[1]
    record = json.loads(REVIEW.read_bytes())
    for row in record['findings']:
        for name in row['evidence_sha256']:
            target = tmp_path / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, target)
    review_path = tmp_path / 'current-review.json'
    review_path.write_bytes(REVIEW.read_bytes())
    assert reviewed_findings([], review_path, tmp_path) == ([], [])
    if change == 'web-context':
        changed = tmp_path / 'compose.yaml'
        before = changed.read_bytes()
        after = before.replace(b'context: ./frontend', b'context: .', 1)
        assert before != after
    elif change == 'new-go-copy':
        changed = tmp_path / 'deployment/docker/Dockerfile.minio-20261112'
        after = changed.read_bytes() + b'COPY deployment/security /tmp/exposed-evidence\n'
    elif change == 'proof':
        changed = tmp_path / 'deployment/security/codeql-continuation-review-20261112.json'
        after = changed.read_bytes() + b'\n'
    elif change == 'addendum':
        changed = tmp_path / record['minio_module_info_addendum']['path']
        after = changed.read_bytes() + b'\n'
    elif change == 'pom':
        changed = tmp_path / 'business-backend/pom.xml'
        before = changed.read_bytes()
        after = before.replace(b'<tomcat.version>11.0.27</tomcat.version>', b'<tomcat.version>11.0.25</tomcat.version>')
        assert before != after
    elif change == 'tomcat-addendum':
        changed = tmp_path / record['tomcat_patch_addendum']['path']
        after = changed.read_bytes() + b'\n'
    elif change == 'license-mode':
        changed = tmp_path / 'deployment/docker/Dockerfile.grafana-20261112'
        before = changed.read_bytes()
        after = before.replace(b'RUN chmod 0755 ', b'RUN chmod 0777 ', 1)
        assert before != after
    elif change == 'license-file':
        changed = tmp_path / 'deployment/security/go-stdlib-20261112-evidence/Go-LICENSE'
        after = changed.read_bytes() + b'\n'
    elif change == 'license-addendum':
        changed = tmp_path / record['grafana_license_directory_addendum']['path']
        after = changed.read_bytes() + b'\n'
    else:
        names = {'grafana-recipe': 'deployment/docker/Dockerfile.grafana-20261112',
                 'stable-helper': 'deployment/vendors/grafana-20261110/prepare_stable_graphs_20261112.py',
                 'original-capture': 'deployment/vendors/grafana-20261107/capture_graphs.py',
                 'plugin-inventory': 'deployment/vendors/grafana-20261107/plugin-modules.json',
                 'stable-addendum': record['grafana_stable_graphs_addendum']['path'],
                 'backend-recipe': 'business-backend/Dockerfile',
                 'backend-benign': 'business-backend/runtime/backend_busybox_benign.py',
                 'backend-ignore': '.dockerignore',
                 'backend-addendum': record['backend_busybox_addendum']['path'],
                 'minio-native-recipe': 'deployment/docker/Dockerfile.minio-20261113',
                 'minio-source-lock': 'deployment/security/minio-os-20261113/source.lock.json',
                 'minio-collector': 'scripts/collect_minio_os_native_20261113.py',
                 'minio-default-local': 'scripts/local.py',
                 'minio-default-inventory': 'scripts/runtime_ci_inventory.py',
                 'minio-workflow': '.github/workflows/security.yml',
                 'cloud-build-overlay': 'deployment/compose/compose.cloud.backend-build.yaml',
                 'cloud-preflight': 'scripts/check_production_config.py',
                 'native-defaults-addendum': record['native_defaults_addendum']['path'],
                 'timeout-template': '.env.example',
                 'timeout-addendum': record['search_timeout_defaults_addendum']['path']}
        changed = tmp_path / names[change]
        after = changed.read_bytes() + b'\n'
    changed.write_bytes(after)
    with pytest.raises(ValueError, match='reviewed source or proof changed'):
        reviewed_findings([], review_path, tmp_path)
