"""Verify complete source findings against the new, separately reviewed build facts."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_build_20261110 as build  # noqa: E402
from scripts import check_grafana_source_fixed_20261107 as retained  # noqa: E402
from scripts import check_grafana_candidate_source_exact2 as candidate  # noqa: E402

old = candidate.old
require = old.require


def bound_file(root, profile, name):
    path = build.checked(root, name)
    require(profile['evidence_sha256'].get(name) == build.digest(path), 'Source technical evidence is not bound')
    return path


def preparation_profile(root, path):
    """An incomplete review can collect facts, but cannot pass the normal gate."""
    profile = old.json_loads(path.read_bytes())
    require(profile.get('schema') == 'grafana-source-build-20261110-v1'
            and profile.get('profile') == build.PROFILE and profile.get('image') == build.IMAGE
            and profile.get('dockerfile') == build.DOCKERFILE and profile.get('context') == '.',
            'Preparation requires the exact source-build identity')
    require(build.DOCKERFILE in profile['source_inputs'], 'The actual new Dockerfile must be bound')
    for section in ('source_inputs', 'evidence_sha256'):
        for name, expected in profile[section].items():
            require(isinstance(expected, str) and build.HEX.fullmatch(expected)
                    and build.digest(build.checked(root, name)) == expected, 'Preparation source/evidence changed')
    overlap = profile['source_inputs'].keys() & profile['evidence_sha256'].keys()
    require(all(profile['source_inputs'][n] == profile['evidence_sha256'][n] for n in overlap),
            'Preparation source/evidence identities disagree')
    return profile


def verify_main(root, profile):
    spec = profile['expected']['source_scan']['main']
    require(spec['elf_sha256'] == profile['expected']['native_sha256'][build.MAIN],
            'The new main graph/native evidence must name the reviewed actual ELF')
    native = old.json_loads(bound_file(root, profile, spec['native']).read_bytes())
    receipt = old.json_loads(bound_file(root, profile, spec['graph_receipt']).read_bytes())
    graph = bound_file(root, profile, spec['graph'])
    require(receipt.get('schema') == 'grafana-candidate-complete-production-graph-v1'
            and receipt.get('completed') is True and receipt.get('exit_code') == 0
            and receipt.get('raw_graph_modified_or_filtered') is False
            and receipt.get('target') == 'main' and receipt.get('package') == './pkg/cmd/grafana'
            and receipt.get('command') == ['go', 'list', '-deps', '-json', '-mod=readonly', './pkg/cmd/grafana']
            and build.digest(graph) == receipt['compressed_sha256']
            and graph.stat().st_size == receipt['compressed_bytes'], 'New main graph collection is incomplete or filtered')
    with gzip.open(graph, 'rb') as stream:
        raw = stream.read(old.MAX_REPORT_BYTES + 1)
    require(len(raw) == receipt['raw_bytes'] <= old.MAX_REPORT_BYTES
            and hashlib.sha256(raw).hexdigest() == receipt['raw_sha256'], 'New main raw graph bytes differ')
    packages, modules, actual = retained.source4.graph_facts(retained.original, raw, native)
    require(len(packages) == receipt['package_count'] and len(modules) == receipt['module_count'],
            'New main full graph/native coverage differs')
    settings = {item['Key']: item['Value'] for item in native['Settings']}
    require(len(settings) == len(native['Settings']) and native['Path'] == spec['entry_package']
            and native['GoVersion'] == spec['go_version']
            and settings.get('vcs.revision') == spec['vcs_revision'] and settings.get('vcs.modified') == 'true'
            and settings.get('-tags', '') == spec['tags'] == '' and settings.get('-trimpath') == 'true'
            and all(settings.get(k) == v for k, v in {'CGO_ENABLED': '0', 'GOOS': 'linux',
                'GOARCH': 'amd64', 'GOAMD64': 'v1'}.items()), 'Actual new main build profile differs')
    require(receipt['compiler'] == 'go version ' + spec['go_version'] + ' linux/amd64'
            and receipt['build_tags'] is None
            and all(receipt['go_environment'].get(k) == settings[k]
                    for k in ('CGO_ENABLED', 'GOOS', 'GOARCH', 'GOAMD64')), 'New main graph compiler profile differs')
    locks = spec['locks']
    require(set(locks) == set(receipt['actual_lock_sha256']) and {'go.mod', 'go.sum'} <= set(locks),
            'Every actual new main source lock is required')
    for name, descriptor in locks.items():
        path = bound_file(root, profile, descriptor['path'])
        require(build.digest(path) == descriptor['sha256'] == receipt['actual_lock_sha256'][name],
                'The graph and actual new main source lock differ')
        if 'source_input' in descriptor:
            require(profile['source_inputs'].get(descriptor['source_input']) == descriptor['sha256'],
                    'The copied lock differs from its declared recipe input')
    thrift = actual.get('github.com/apache/thrift', {})
    require(thrift.get('Version') == 'v0.25.0' and not thrift.get('Replace'),
            'Actual new main must compile the official fixed Thrift version')
    return {'target': build.MAIN, 'elf_sha256': profile['expected']['native_sha256'][build.MAIN],
            'native': actual, 'packages': sorted(packages), 'package_count': len(packages), 'module_count': len(modules)}


def verify_compiled(root, profile):
    request = retained.api.load_request(root)
    scope = retained.api.read_json(root / request['binding']['source_scope_file'])
    facts = retained.verify_profiles(root, request, scope)
    baseline = request['binding']['binary_sha256']
    expected = profile['expected']['native_sha256']
    require(set(expected) == set(baseline) and len(expected) == 14
            and expected[build.MAIN] != baseline[build.MAIN]
            and all(expected[n] == digest for n, digest in baseline.items() if n != build.MAIN),
            'Only the new main and thirteen unchanged plugin ELFs are allowed')
    facts['main'] = verify_main(root, profile)
    require({v['target']: v['elf_sha256'] for v in facts.values()} == expected,
            'All fourteen actual compiled subjects must match the new build')
    return facts


def verify_regression(root, profile, name):
    envelope = old.json_loads(bound_file(root, profile, name).read_bytes())
    sources = envelope.get('source_inputs')
    raw = bound_file(root, profile, envelope['raw_receipt'])
    require(envelope.get('schema') == 'grafana-source-regression-envelope-v1'
            and build.digest(raw) == envelope['raw_receipt_sha256'] and isinstance(sources, dict) and sources
            and all(profile['source_inputs'].get(n) == sha for n, sha in sources.items()),
            'Actual source regression receipt is missing or belongs to another source')
    receipt = old.json_loads(raw.read_bytes())
    require(receipt.get('passed') is True, 'The original regression did not pass')
    return envelope, receipt


def verify_busybox(root, profile):
    spec = profile['expected']['source_scan']['busybox']
    prefix = 'deployment/vendors/grafana-20261110/busybox/'
    sources = {name for name in profile['source_inputs'] if name.startswith(prefix)}
    required = {prefix + name for name in ('source.lock.json', 'busybox.config', 'regression.py',
                'build.sh', 'Dockerfile', 'wget-http-request-boundary.patch')}
    require(isinstance(spec['source_inputs'], list) and len(spec['source_inputs']) == len(sources)
            and set(spec['source_inputs']) == sources and required <= sources,
            'Every real BusyBox source, configuration, patch and build input must be bound')
    envelope, raw = verify_regression(root, profile, spec['regression_receipt'])
    require(set(spec['source_inputs']) <= envelope['source_inputs'].keys(),
            'BusyBox regression does not bind every reviewed source/patch input')
    cases = raw['cases']
    labels = {case['label'] for case in cases}
    required = {'reject-direct-crlf-injection', 'reject-proxy-host-injection',
                'normal/redirect-clean', 'normal/redirect-encoded', 'reject/redirect-space',
                'reject/redirect-absolute-space', 'reject/redirect-c0', 'normal-proxy-encoded',
                'normal-post-encoded', 'normal-authentication-space',
                *('reject-raw-target-' + hex(byte) for byte in [*range(1, 33), 127])}
    require(raw['binary_sha256'] == spec['binary_sha256'] and build.HEX.fullmatch(spec['binary_sha256'])
            and raw['case_count'] == len(cases) >= 50 and len(labels) == len(cases) and required <= labels
            and all(case['variant'] == 'fixed' for case in cases)
            and 'Docker --network none' in raw['all_injection_traffic']
            and raw.get('nul_boundary') and raw.get('redirect_boundary'),
            'BusyBox binary, isolated traffic boundary or actual regression coverage differs')
    for case in cases:
        if case['label'].startswith('reject'):
            expected_connections = 1 if case['label'].startswith('reject/redirect-') else 0
            require(case['exit_code'] != 0 and case['connections'] == expected_connections
                    and len(case['requests_hex']) == expected_connections, 'An injection rejection failed')
        else:
            require(case['exit_code'] == 0 and case['connections'] == len(case['requests_hex']) >= 1,
                    'A valid BusyBox request regressed')
        for request_hex in case['requests_hex']:
            lines = bytes.fromhex(request_hex).split(b'\r\n')
            first = lines[0].split(b' ')
            require(len(first) == 3 and first[0] in {b'GET', b'POST'} and first[2] == b'HTTP/1.1'
                    and first[1] and all(32 < byte < 127 for byte in first[1])
                    and not any(line.lower().startswith((b'x-injected:', b'x-host-canary:')) for line in lines[1:]),
                    'Raw BusyBox request contains an injected or malformed request boundary')


def verify_proof(root, profile, row, compiled):
    allowed = {'FIXED', 'NOT_AFFECTED'}
    if row.get('proof', {}).get('kind') == 'maintained_patch':
        allowed = {'MAINTAINED_REPLACEMENT_VERIFIED'}
    require(row.get('disposition') in allowed and row.get('technical_basis')
            and isinstance(row.get('evidence'), list) and row['evidence'], 'Each source finding requires a technical disposition')
    for name in row['evidence']:
        bound_file(root, profile, name)
    proof = row['proof']
    if proof['kind'] == 'compiled_dependency':
        fact = compiled[proof['profile']]
        require(proof['module'] in fact['native'] and fact['native'][proof['module']] == proof['expected_identity'],
                'Claimed fixed dependency is not the actual compiled identity')
    elif proof['kind'] == 'compiled_absence':
        fact = compiled[proof['profile']]
        prefixes = proof['package_prefixes']
        require(isinstance(prefixes, list) and prefixes and all(isinstance(p, str) and p for p in prefixes)
                and not any(p == prefix or p.startswith(prefix + '/') for p in fact['packages'] for prefix in prefixes),
                'An affected package is present in the actual complete graph')
    elif proof['kind'] == 'source_regression':
        require(proof['receipt'] in row['evidence'], 'The actual regression receipt must be cited')
        verify_regression(root, profile, proof['receipt'])
    elif proof['kind'] in {'external_graph', 'standalone_context', 'maintained_patch'}:
        from scripts import grafana_source_context_20261110 as context
        getattr(context, proof['kind'])(root, profile, row, proof)
    else:
        raise ValueError('Unsupported source finding proof; an unverified waiver cannot pass')


def source_facts(scan):
    findings, counts, secrets = old.summarize(scan)
    require(not any(secrets.values()), 'Every source secret blocks')
    targets = sorted([r['Target'], r.get('Class', ''), r.get('Type', '')] for r in scan['Results'])
    require(len({tuple(row) for row in targets}) == len(targets), 'Duplicate source targets cannot pass')
    inventories = {r['Target']: hashlib.sha256(old.canonical(old.inventory(r['Packages']))).hexdigest()
                   for r in scan['Results'] if r.get('Class') == 'lang-pkgs'}
    rows = [{k: row[k] for k in ('id', 'target', 'package', 'version', 'severity')}
            for row in findings if row['kind'] == 'vulnerability']
    return {'scan_targets': targets, 'target_inventory_sha256': inventories, 'findings': rows}, counts, findings


def assess_findings(root, profile, observed, compiled):
    scope = profile['expected']['source_scan']
    require(scope['scan_targets'] == observed['scan_targets']
            and scope['target_inventory_sha256'] == observed['target_inventory_sha256'],
            'Complete current source target/package coverage differs from the review')
    actual = Counter(map(build.finding_key, observed['findings']))
    reviewed = Counter(map(build.finding_key, scope['reviewed_findings']))
    require(actual == reviewed and all(v == 1 for v in reviewed.values()),
            'All-severity source findings are missing, extra, duplicate or reclassified')
    for row in scope['reviewed_findings']:
        verify_proof(root, profile, row, compiled)


def evaluate(scan, receipt, original_verdict, candidate_verdict, *, raw_sha256, original_sha256,
             source_commit, root=ROOT, now=None, tracked_clean=True, expected_targets=None,
             prepare=False, profile=None, runtime_inventory=None, compiled_root=None):
    now = now or datetime.now(timezone.utc)
    profile = profile if prepare else build.load_profile(root)
    observed, counts, findings = source_facts(scan)
    expected_targets = expected_targets or old.committed_manifest_targets(root, source_commit)
    _, legacy_scope, _ = old.load_scope(root, old.POLICY, now)
    old.validate_completion(scan, receipt, raw_sha256, legacy_scope, source_commit, tracked_clean,
                            findings, now, expected_targets=expected_targets)
    prior = candidate.evaluate(scan, receipt, original_verdict, root=root, source_commit=source_commit,
        raw_sha256=raw_sha256, now=now, tracked_clean=tracked_clean, expected_targets=expected_targets)
    require({k: v for k, v in candidate_verdict.items() if k != 'preserved_original_verdict_file_sha256'} == prior
            and candidate_verdict.get('preserved_original_verdict_file_sha256') == original_sha256,
            'Preserved original/candidate source verdicts differ from independent recomputation')
    # Scanner completion and current manifests still use root; only a caller's
    # separately verified historical compilation may use its immutable source.
    compiled = verify_compiled(root if compiled_root is None else compiled_root, profile)
    rows = observed['findings']
    result = {'passed': False, 'operational_collection': True, 'scan_completion_verified': True,
        'mode': 'source_build_preparation', 'profile': build.PROFILE, 'raw_counts': counts,
        'raw_report_modified': False, 'unresolved_count': len(rows), 'binary_redistribution_approved': False,
        'old_authorization_inherited': False, 'preserved_original_failed_verdict': original_verdict,
        'preserved_candidate_failed_verdict': candidate_verdict,
        'observed': {**observed, 'compiled': compiled}}
    if prepare:
        return result
    assess_findings(root, profile, observed, compiled)
    verify_busybox(root, profile)
    from scripts import runtime_ci_inventory
    require(runtime_inventory == runtime_ci_inventory.build_record(root)
            and runtime_inventory['grafana_selection']['profile'] == build.PROFILE
            and runtime_inventory['grafana_selection']['mode'] == 'source_build',
            'Actual current Compose/CI selection does not select this reviewed source build')
    result.update(passed=True, mode='complete_source_build_applicability_verified', unresolved_count=0,
                  verified_all_severity_findings=len(rows), technical_ready=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('scan', 'scan-receipt', 'original-verdict', 'candidate-verdict', 'runtime-inventory', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--profile', type=Path, default=ROOT / build.PROFILE_PATH,
                        help='Draft profile for --prepare only; the normal gate uses the fixed reviewed profile')
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink() or not args.output.resolve().is_relative_to(ROOT / '.cache'):
        print(json.dumps({'passed': False, 'error': 'Use a new diagnostic output under this repository .cache'}))
        return 1
    try:
        require(args.scan.is_file() and not args.scan.is_symlink()
                and 0 < args.scan.stat().st_size <= old.MAX_REPORT_BYTES, 'Complete raw source scan is unavailable')
        scan, receipt, original, previous, inventory = [old.json_loads(path.read_bytes()) for path in
            (args.scan, args.scan_receipt, args.original_verdict, args.candidate_verdict, args.runtime_inventory)]
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT, text=True)
        profile = preparation_profile(ROOT, args.profile) if args.prepare else None
        result = evaluate(scan, receipt, original, previous, raw_sha256=build.digest(args.scan),
            original_sha256=build.digest(args.original_verdict), source_commit=head, tracked_clean=not dirty,
            profile=profile, prepare=args.prepare, runtime_inventory=inventory)
    except (ValueError, TypeError, KeyError, OSError, EOFError, UnicodeError, subprocess.SubprocessError) as error:
        result = {'passed': False, 'mode': 'source_build_failed_closed', 'binary_redistribution_approved': False,
                  'error_type': type(error).__name__, 'error': str(error) if isinstance(error, ValueError)
                  else 'Exact complete source-build evidence is unavailable'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as stream:
        stream.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('passed', 'mode', 'unresolved_count', 'error') if k in result}))
    return 0 if result['passed'] or args.prepare and result.get('operational_collection') else 1


if __name__ == '__main__':
    raise SystemExit(main())
