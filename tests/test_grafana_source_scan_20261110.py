"""Source-build graph, all-severity and real regression evidence boundaries."""
from copy import deepcopy
import gzip
import hashlib
import json

import pytest

from scripts import check_grafana_source_scan_20261110 as gate


def put(root, profile, name, value, *, raw=False):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if raw else json.dumps(value).encode())
    profile['evidence_sha256'][name] = gate.build.digest(path)
    return path


@pytest.fixture
def main_graph(tmp_path):
    module = 'github.com/grafana/grafana'
    thrift = {'Path': 'github.com/apache/thrift', 'Version': 'v0.25.0', 'Sum': 'h1:fixture'}
    settings = {'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': 'amd64', 'GOAMD64': 'v1',
                'vcs.revision': 'c' * 40, 'vcs.modified': 'true', '-trimpath': 'true'}
    native = {'Main': {'Path': module, 'Version': '(devel)'}, 'Deps': [thrift],
              'GoVersion': 'go1.27.3', 'Path': module + '/pkg/cmd/grafana',
              'Settings': [{'Key': k, 'Value': v} for k, v in settings.items()]}
    rows = [{'ImportPath': native['Path'], 'Imports': [thrift['Path'] + '/lib/go/thrift'],
             'Module': {'Path': module, 'Main': True}},
            {'ImportPath': thrift['Path'] + '/lib/go/thrift', 'Imports': [], 'Module': thrift}]
    raw = b'\n'.join(json.dumps(row).encode() for row in rows)
    profile = {'source_inputs': {}, 'evidence_sha256': {},
               'expected': {'native_sha256': {gate.build.MAIN: 'd' * 64}, 'source_scan': {}}}
    compressed = gzip.compress(raw, mtime=0)
    graph = put(tmp_path, profile, 'evidence/main.json.gz', compressed, raw=True)
    put(tmp_path, profile, 'evidence/native.json', native)
    locks = {}
    for name in ('go.mod', 'go.sum'):
        path = put(tmp_path, profile, 'evidence/locks/' + name, ('fixture-' + name).encode(), raw=True)
        locks[name] = {'path': path.relative_to(tmp_path).as_posix(), 'sha256': gate.build.digest(path)}
    receipt = {'schema': 'grafana-candidate-complete-production-graph-v1', 'target': 'main',
        'package': './pkg/cmd/grafana', 'command': ['go', 'list', '-deps', '-json', '-mod=readonly', './pkg/cmd/grafana'],
        'completed': True, 'exit_code': 0, 'raw_graph_modified_or_filtered': False,
        'compressed_sha256': gate.build.digest(graph), 'compressed_bytes': len(compressed),
        'raw_bytes': len(raw), 'raw_sha256': hashlib.sha256(raw).hexdigest(),
        'package_count': 2, 'module_count': 2, 'build_tags': None,
        'compiler': 'go version go1.27.3 linux/amd64', 'go_environment': settings,
        'actual_lock_sha256': {n: spec['sha256'] for n, spec in locks.items()}}
    put(tmp_path, profile, 'evidence/receipt.json', receipt)
    profile['expected']['source_scan']['main'] = {'native': 'evidence/native.json',
        'elf_sha256': 'd' * 64,
        'graph': 'evidence/main.json.gz', 'graph_receipt': 'evidence/receipt.json', 'locks': locks,
        'entry_package': native['Path'], 'go_version': native['GoVersion'], 'vcs_revision': 'c' * 40, 'tags': ''}
    return tmp_path, profile, native, receipt, raw


def test_full_new_main_graph_accepts_actual_fixed_thrift(main_graph):
    root, profile, *_ = main_graph
    actual = gate.verify_main(root, profile)
    assert actual['package_count'] == 2
    assert actual['native']['github.com/apache/thrift']['Version'] == 'v0.25.0'


@pytest.mark.parametrize('change', ['filtered', 'missing_lock', 'compiler', 'raw_sha', 'incomplete_closure', 'old_thrift', 'different_elf'])
def test_new_main_graph_rejects_changed_facts(main_graph, change):
    root, profile, native, receipt, raw = main_graph
    if change == 'filtered':
        receipt['raw_graph_modified_or_filtered'] = True
    elif change == 'missing_lock':
        profile['expected']['source_scan']['main']['locks'].pop('go.sum')
    elif change == 'compiler':
        receipt['go_environment'] = {**receipt['go_environment'], 'CGO_ENABLED': '1'}
    elif change == 'raw_sha':
        receipt['raw_sha256'] = 'a' * 64
    elif change == 'different_elf':
        profile['expected']['source_scan']['main']['elf_sha256'] = 'e' * 64
    elif change == 'incomplete_closure':
        raw = raw.split(b'\n')[0]
        graph = put(root, profile, 'evidence/main.json.gz', gzip.compress(raw, mtime=0), raw=True)
        receipt.update(raw_bytes=len(raw), raw_sha256=hashlib.sha256(raw).hexdigest(),
                       compressed_sha256=gate.build.digest(graph), compressed_bytes=graph.stat().st_size)
    else:
        native['Deps'][0]['Version'] = 'v0.24.0'
        raw = raw.replace(b'v0.25.0', b'v0.24.0')
        graph = put(root, profile, 'evidence/main.json.gz', gzip.compress(raw, mtime=0), raw=True)
        receipt.update(raw_sha256=hashlib.sha256(raw).hexdigest(), compressed_sha256=gate.build.digest(graph))
    put(root, profile, 'evidence/native.json', native)
    put(root, profile, 'evidence/receipt.json', receipt)
    with pytest.raises(ValueError):
        gate.verify_main(root, profile)


@pytest.fixture
def source_rows(tmp_path):
    scan = {'Results': [{'Target': 'new/input/go.mod', 'Class': 'lang-pkgs', 'Type': 'gomod',
        'Packages': [{'Name': 'module', 'Version': 'v1.0.0'}],
        'Vulnerabilities': [{'VulnerabilityID': 'CVE-LOW', 'PkgName': 'module', 'InstalledVersion': 'v1.0.0', 'Severity': 'LOW'},
                            {'VulnerabilityID': 'CVE-MEDIUM', 'PkgName': 'module', 'InstalledVersion': 'v1.0.0', 'Severity': 'MEDIUM'}]}]}
    observed, _, _ = gate.source_facts(scan)
    profile = {'source_inputs': {}, 'evidence_sha256': {}, 'expected': {'source_scan': {
        **observed, 'reviewed_findings': []}}}
    put(tmp_path, profile, 'evidence/proof.json', {'actual': 'fixture closure'})
    for row in observed['findings']:
        profile['expected']['source_scan']['reviewed_findings'].append({**row, 'disposition': 'NOT_AFFECTED',
            'technical_basis': 'The exact affected package is absent from the complete fixture closure',
            'evidence': ['evidence/proof.json'],
            'proof': {'kind': 'compiled_absence', 'profile': 'main', 'package_prefixes': ['module/affected']}})
    compiled = {'main': {'packages': ['module/actually-compiled'], 'native': {}}}
    return tmp_path, profile, observed, compiled, scan


def test_source_scope_uses_dynamic_inventory_and_every_severity(source_rows):
    root, profile, observed, compiled, _ = source_rows
    gate.assess_findings(root, profile, observed, compiled)
    assert len(observed['scan_targets']) == 1


@pytest.mark.parametrize('change', ['omit_low', 'omit_medium', 'duplicate', 'inventory', 'target', 'affected_package', 'waiver'])
def test_source_scope_rejects_missing_or_unproved_facts(source_rows, change):
    root, profile, observed, compiled, _ = source_rows
    scope = profile['expected']['source_scan']
    if change == 'omit_low':
        scope['reviewed_findings'] = scope['reviewed_findings'][1:]
    elif change == 'omit_medium':
        scope['reviewed_findings'] = scope['reviewed_findings'][:1]
    elif change == 'duplicate':
        observed['findings'].append(deepcopy(observed['findings'][0]))
    elif change == 'inventory':
        scope['target_inventory_sha256'] = {'new/input/go.mod': 'a' * 64}
    elif change == 'target':
        scope['scan_targets'] = []
    elif change == 'affected_package':
        compiled['main']['packages'].append('module/affected/decode')
    else:
        scope['reviewed_findings'][0]['proof']['kind'] = 'risk_accepted'
    with pytest.raises(ValueError):
        gate.assess_findings(root, profile, observed, compiled)


@pytest.mark.parametrize('change', ['secret', 'suppressed', 'duplicate_target'])
def test_source_collection_rejects_secrets_filters_and_ambiguous_targets(source_rows, change):
    *_, scan = source_rows
    if change == 'secret':
        scan['Results'][0]['Secrets'] = [{'RuleID': 'fixture', 'Severity': 'LOW', 'Match': 'never output this'}]
    elif change == 'suppressed':
        scan['Results'][0]['SuppressedFindings'] = [{'id': 'anything'}]
    else:
        scan['Results'].append(deepcopy(scan['Results'][0]))
    with pytest.raises(ValueError):
        gate.source_facts(scan)


def test_source_default_requires_complete_new_profile_before_legacy_evidence(tmp_path):
    with pytest.raises(ValueError, match='required exact source'):
        gate.evaluate({}, {}, {}, {}, raw_sha256='a' * 64, original_sha256='b' * 64,
                      source_commit='c' * 40, root=tmp_path)


@pytest.fixture
def busybox_receipt(tmp_path):
    sources = {}
    for name in ('source.lock.json', 'busybox.config', 'regression.py', 'build.sh', 'Dockerfile',
                 'wget-http-request-boundary.patch'):
        source = tmp_path / 'deployment/vendors/grafana-20261110/busybox' / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('fixture input bytes for ' + name)
        sources[source.relative_to(tmp_path).as_posix()] = gate.build.digest(source)
    profile = {'source_inputs': sources, 'evidence_sha256': {},
               'expected': {'source_scan': {'busybox': {'source_inputs': list(sources),
                    'regression_receipt': 'evidence/envelope.json', 'binary_sha256': 'f' * 64}}}}
    labels = ['reject-direct-crlf-injection', 'reject-proxy-host-injection',
              'normal/redirect-clean', 'normal/redirect-encoded', 'reject/redirect-space',
              'reject/redirect-absolute-space', 'reject/redirect-c0', 'normal-proxy-encoded',
              'normal-post-encoded', 'normal-authentication-space',
              *('reject-raw-target-' + hex(byte) for byte in [*range(1, 33), 127])]
    labels += ['normal-fixture-' + str(i) for i in range(50 - len(labels))]
    cases = []
    for label in labels:
        reject = label.startswith('reject')
        connections = 1 if not reject or label.startswith('reject/redirect-') else 0
        cases.append({'label': label, 'variant': 'fixed', 'exit_code': 1 if reject else 0,
                      'connections': connections, 'requests_hex':
                      [b'GET /fixture HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n'.hex()] * connections})
    raw = {'passed': True, 'case_count': 50, 'cases': cases, 'binary_sha256': 'f' * 64,
           'all_injection_traffic': 'Fixture transcript boundary: Docker --network none',
           'nul_boundary': 'Fixture encoded argv boundary', 'redirect_boundary': 'Fixture redirected request boundary'}
    path = put(tmp_path, profile, 'evidence/raw.json', raw)
    envelope = {'schema': 'grafana-source-regression-envelope-v1', 'source_inputs': deepcopy(profile['source_inputs']),
                'raw_receipt': 'evidence/raw.json', 'raw_receipt_sha256': gate.build.digest(path)}
    put(tmp_path, profile, 'evidence/envelope.json', envelope)
    return tmp_path, profile, raw, envelope


def test_busybox_checks_original_transcript_and_source_envelope(busybox_receipt):
    root, profile, *_ = busybox_receipt
    gate.verify_busybox(root, profile)


@pytest.mark.parametrize('change', ['binary', 'case_count', 'missing_control', 'injected_header',
                                    'failed', 'source_drift', 'raw_drift', 'reject_made_connection', 'omitted_source'])
def test_busybox_passed_flag_alone_cannot_close_source_finding(busybox_receipt, change):
    root, profile, raw, envelope = busybox_receipt
    if change == 'binary':
        raw['binary_sha256'] = 'e' * 64
    elif change == 'case_count':
        raw['case_count'] = 49
    elif change == 'missing_control':
        raw['cases'][10]['label'] = 'normal-replacement'
    elif change == 'injected_header':
        case = next(c for c in raw['cases'] if c['label'] == 'normal/redirect-clean')
        case['requests_hex'][0] = b'GET /fixture HTTP/1.1\r\nX-Injected: bad\r\n\r\n'.hex()
    elif change == 'failed':
        raw['passed'] = False
    elif change == 'source_drift':
        envelope['source_inputs'][next(iter(profile['source_inputs']))] = 'a' * 64
    elif change == 'reject_made_connection':
        raw['cases'][0]['connections'] = 1
        raw['cases'][0]['requests_hex'] = [b'GET /bad HTTP/1.1\r\n\r\n'.hex()]
    elif change == 'omitted_source':
        profile['expected']['source_scan']['busybox']['source_inputs'].pop()
    else:
        envelope['raw_receipt_sha256'] = 'a' * 64
    path = put(root, profile, 'evidence/raw.json', raw)
    if change != 'raw_drift':
        envelope['raw_receipt_sha256'] = gate.build.digest(path)
    put(root, profile, 'evidence/envelope.json', envelope)
    with pytest.raises(ValueError):
        gate.verify_busybox(root, profile)
