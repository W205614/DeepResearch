"""Scan full Go runtimes and bind exact applicability facts to current ELF and source."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.capture_go_native_20261112 import capture as capture_native  # noqa: E402
from scripts.grafana_source_build_facts_20261110 import capture  # noqa: E402
from scripts.go_stdlib_rebuild_20261112 import TOOLCHAIN, require, verify_trivy_source  # noqa: E402
from scripts.check_image_secrets import assess as assess_secrets  # noqa: E402
from scripts import check_grafana_source_build_20261110 as shared  # noqa: E402

TARGETS = {
    'alertmanager': {'bin/alertmanager': 'alertmanager', 'bin/amtool': 'amtool'},
    'etcd': {'usr/local/bin/' + name: name for name in ('etcd', 'etcdctl', 'etcdutl')},
    'loki': {'usr/bin/loki': 'loki'},
    'prometheus': {'bin/prometheus': 'prometheus', 'bin/promtool': 'promtool'},
    'otel-collector': {'otelcol-contrib': 'otel'},
    'milvus': {'milvus/bin/milvus': 'milvus_main',
               'milvus/lib/libmilvus-planparser.so': 'milvus_parser'},
    'storage-init': {'storage-bootstrap': 'storage-bootstrap'},
    'tempo': {'tempo': 'tempo'},
}
TEMPO_PROOF = 'deployment/security/tempo-all-severity-20261115-proof.json'
TEMPO_PROOF_SHA256 = 'fe0becca7b18ca0ee6c5a9e3bd29a982d7e6f7dbfff6751cfe832a551b47f891'


def module_identity(module):
    value = {k: module[k] for k in ('Path', 'Version', 'Sum') if k in module}
    if module.get('Main') or value.get('Version') in (None, '(devel)'):
        value['Version'] = '(devel)'
    if 'Replace' in module:
        value['Replace'] = module_identity(module['Replace'])
    return value


def verify_graph(native, receipt, packages):
    require(native['Path'] in packages, 'Actual Go entry point differs from the complete source graph')
    entry = packages[native['Path']]
    require(entry.get('Name') == 'main' and not entry.get('DepOnly') and not entry.get('ForTest'),
            'The actual entry must be a production main package in the collected context')
    if 'Module' in entry:
        require(entry['Module']['Path'] == native['Main'].get('Path'),
                'The graph entry does not belong to the actual executable main module')
    else:
        require(native['Path'] == 'command-line-arguments' and native['Main'] == {}
                and receipt['command'][-1].endswith('.go'),
                'A module-free entry must be the exact Go file build context')
    # The official `go version -m -json` omits Value for empty CGO flags.
    settings = {row['Key']: row.get('Value', '') for row in native['Settings']}
    for name in ('GOOS', 'GOARCH', 'CGO_ENABLED'):
        require(settings[name] == receipt['environment'][name], 'Actual native build setting differs: ' + name)
    require(native['GoVersion'] in receipt['compiler'].split(), 'Native compiler differs from source graph')
    expected_version = 'go1.26.11' if native['GoVersion'].startswith('go1.26.') else 'go1.27.4'
    require(native['GoVersion'] == expected_version, 'Actual Go runtime still uses the affected standard library')
    tags = [arg.split('=', 1)[1] for arg in receipt['command'] if arg.startswith('-tags=')]
    require(settings.get('-tags', '') == (tags[0] if tags else ''), 'Actual Go build tags differ from source graph')
    modules = {}
    for row in packages.values():
        if 'Module' not in row:
            continue
        module = module_identity(row['Module'])
        previous = modules.setdefault(module['Path'], module)
        require(previous == module, 'Source graph contains conflicting module identities')
    native_modules = [*([native['Main']] if native['Main'] else []), *native.get('Deps', [])]
    actual = {row['Path']: module_identity(row) for row in native_modules}
    require(len(actual) == len(native_modules), 'Actual native module identities are duplicated')
    main = native['Main']
    if main and main['Path'] in modules and main.get('Version', '(devel)') != '(devel)':
        # go list intentionally disables VCS stamping; newer Go builds stamp the
        # main module from Git. Bind that stamp to the verified upstream commit.
        provenance = receipt['source_provenance']
        if receipt.get('profile') in {'etcd', 'etcdctl', 'etcdutl'}:
            lock_path = 'deployment/vendors/etcd/source.lock.json'
            require(shared.digest(ROOT / lock_path) == receipt['source_inputs'][lock_path],
                    'Etcd VCS version source lock differs from the collected graph')
            lock = json.loads((ROOT / lock_path).read_bytes())
            commit, source_version = provenance['commit'], lock['tag']
            require(commit == lock['commit'] and provenance['archive_sha256'] == lock['source_archive_sha256'],
                    'Etcd archive, upstream commit or source tag differs')
        else:
            commit, source_version = provenance['Origin']['Hash'], provenance['Version']
        require(settings.get('vcs.revision') == commit,
                'Actual main module VCS stamp differs from verified upstream source')
        version = main['Version'].removesuffix('+dirty')
        require(version == source_version
                or re.fullmatch(r'v\d+\.\d+\.\d+-(?:0\.)?\d{14}-' + re.escape(commit[:12]), version),
                'Actual main module version is neither the verified source version nor its exact Git pseudo-version')
        modules[main['Path']]['Version'] = main['Version']
    require(modules == actual, 'Complete source graph and actual ELF module identities differ')
    reached, pending = set(), [native['Path']]
    while pending:
        name = pending.pop()
        if name == 'C':
            require(settings['CGO_ENABLED'] == '1', 'A C import requires the actual CGO build mode')
            # cmd/go adds runtime/cgo as a compiler import; go list retains it
            # in Deps but does not expose this edge in source Imports.
            if 'runtime/cgo' not in reached:
                pending.append('runtime/cgo')
            continue
        if name in reached:
            continue
        require(name in packages, 'The production entry import closure is incomplete')
        reached.add(name)
        row = packages[name]
        pending.extend(row.get('ImportMap', {}).get(value, value) for value in row.get('Imports', []))
    require(reached == packages.keys(), 'The graph contains packages outside the actual production entry closure')


def load_tempo_proof():
    from scripts import check_tempo_app_exact1 as high
    path = ROOT / TEMPO_PROOF
    require(hashlib.sha256(path.read_bytes()).hexdigest() == TEMPO_PROOF_SHA256,
            'Exact supplementary Tempo source proof changed')
    proof = json.loads(path.read_bytes())
    require(proof['schema'] == 'tempo-all-severity-fixed-source-proof-20261115-v1'
            and proof['status'] == 'verified' and proof['facts_passed'] is True
            and proof['actor_kind'] == 'automated_independent_agent'
            and proof['actor'] != 'PENDING_INDEPENDENT_REVIEW'
            and proof['risk_acceptance'] is False and proof['binary_redistribution_approved'] is False,
            'Supplementary Tempo source proof requires actual independent technical review')
    review = high.load_review()
    require(proof['original_high_review_sha256'] == high.file_hash(ROOT / high.REVIEW_PATH)
            and proof['actual_elf_sha256'] == review['scope']['elf_sha256']
            and proof['actual_module_version'] == high.VERSION
            and proof['go_version'] == 'go1.27.4'
            and proof['current_source_commit'] == review['scope']['upstream_commit'],
            'Supplementary Tempo source scope differs from the original exact ELF review')
    for relative, expected in proof['evidence_sha256'].items():
        require(shared.digest(shared.checked(ROOT, relative)) == expected,
                'Supplementary exact Tempo source evidence changed: ' + relative)
    return proof


def evaluate_findings(scan, graphs, tempo_proof=None, milvus_os_scope=None):
    require(assess_secrets(scan)['passed'], 'Every image secret blocks; private raw report retained')
    unresolved, dispositions = [], []
    for result in scan['Results']:
        for finding in result.get('Vulnerabilities', []):
            target = result['Target'].lstrip('/')
            if (finding['VulnerabilityID'] == 'GO-2026-5932'
                    and finding['PkgName'] == 'golang.org/x/crypto'
                    and finding['Severity'] == 'UNKNOWN' and target in graphs):
                packages, native = graphs[target]
                module = next((m for m in native.get('Deps', []) if m['Path'] == 'golang.org/x/crypto'), None)
                require(module is not None and module['Version'] == finding['InstalledVersion'],
                        'OpenPGP finding differs from actual native module')
                prefix = 'golang.org/x/crypto/openpgp'
                require(not any(name == prefix or name.startswith(prefix + '/') for name in packages),
                        'Officially affected OpenPGP package enters the complete compiled closure')
                dispositions.append({'id': finding['VulnerabilityID'], 'target': target,
                    'package': finding['PkgName'], 'version': finding['InstalledVersion'],
                    'severity': finding['Severity'], 'disposition': 'AFFECTED_PACKAGE_NOT_COMPILED',
                    'risk_acceptance': False})
            elif milvus_os_scope is not None and result.get('Class') == 'os-pkgs':
                from scripts.check_milvus_os_20261115 import finding_key
                key = finding_key(finding)
                require(key in milvus_os_scope, 'An unreviewed Milvus OS tuple cannot inherit another fact')
                reviewed = milvus_os_scope[key]
                dispositions.append({'id': finding['VulnerabilityID'], 'target': target,
                    'package': finding['PkgName'], 'version': finding['InstalledVersion'],
                    'severity': finding['Severity'], 'disposition': reviewed['disposition'],
                    'scope': 'EXACT_CURRENT_MILVUS_STANDALONE_ROLE', 'risk_acceptance': False})
            elif tempo_proof is not None and target == 'tempo' and target in graphs:
                _, native = graphs[target]
                exact = {'CVE-2026-28377': ('HIGH', '2.10.3'),
                         'CVE-2026-27878': ('MEDIUM', '1.5.1-0.20260303204923-b13f74291d48')}
                if finding['VulnerabilityID'] in exact:
                    severity, fixed_version = exact[finding['VulnerabilityID']]
                    require(finding['PkgName'] == native['Main']['Path'] == 'github.com/grafana/tempo'
                            and finding['InstalledVersion'] == native['Main']['Version']
                            == tempo_proof['actual_module_version']
                            and finding['Severity'] == severity and finding['FixedVersion'] == fixed_version,
                            'An exact Tempo finding changed package, version, severity or fix boundary')
                    dispositions.append({'id': finding['VulnerabilityID'], 'target': target,
                        'package': finding['PkgName'], 'version': finding['InstalledVersion'],
                        'severity': severity, 'disposition': 'FIXED_IN_EXACT_BOUND_ELF_SOURCE',
                        'risk_acceptance': False})
                else:
                    unresolved.append({key: finding[key] for key in
                                       ('VulnerabilityID', 'PkgName', 'InstalledVersion', 'Severity')}
                                      | {'target': target})
            else:
                unresolved.append({key: finding[key] for key in
                                   ('VulnerabilityID', 'PkgName', 'InstalledVersion', 'Severity')}
                                  | {'target': target})
    return {'passed': not unresolved, 'unresolved': unresolved,
            'technical_dispositions': dispositions, 'raw_report_modified': False}


def verify_go_inventory(result, native):
    expected = Counter()
    for row in [*([native['Main']] if native['Main'] else []), *native.get('Deps', [])]:
        effective = row.get('Replace', row)
        version = effective.get('Version')
        expected[effective['Path'], None if version == '(devel)' else version] += 1
    expected['stdlib', 'v' + native['GoVersion'][2:]] += 1
    require(type(result.get('Packages')) is list and result['Packages']
            and Counter((row['Name'], row.get('Version')) for row in result['Packages']) == expected,
            'The full Go scanner package inventory differs from the actual ELF modules')


def run(args):
    from scripts.source_dependency_migration_20261115 import load_current_context
    out = args.output_dir.resolve()
    require(out.is_relative_to(ROOT / '.cache') and not out.exists(), 'Use a new evidence directory under .cache')
    out.mkdir(parents=True)
    raw, config, ignore = out / 'raw.private.json', out / 'empty.yaml', out / 'empty.ignore'
    config.write_bytes(b'{}\n')
    ignore.write_bytes(b'')
    environment = {k: v for k, v in os.environ.items() if not k.startswith('TRIVY_')}
    version = subprocess.check_output([args.scanner, '--version'], env=environment)
    require(b'Version: 0.75.0' in version, 'The pinned complete scanner is required')
    (out / 'scanner-version.log').write_bytes(version)
    command = [args.scanner, 'image', '--config', str(config), '--ignorefile', str(ignore),
        '--scanners', 'vuln,secret', '--severity', 'UNKNOWN,LOW,MEDIUM,HIGH,CRITICAL',
        '--list-all-pkgs', '--ignore-unfixed=false', '--format', 'json', '--output', str(raw),
        '--exit-code', '0', '--timeout', '10m', args.image]
    if args.cache_dir is not None:
        command[2:2] = ['--cache-dir', str(args.cache_dir.resolve())]
    start = datetime.now(timezone.utc)
    with (out / 'scanner.log').open('wb') as stream:
        process = subprocess.run(command, env=environment, stdout=stream, stderr=stream, timeout=630)
    finish = datetime.now(timezone.utc)
    (out / 'scan-completion.json').write_text(json.dumps({'command': command,
        'started_at': start.isoformat(), 'finished_at': finish.isoformat(),
        'process_exit_code': process.returncode, 'filters': [], 'all_severities': True}, indent=2))
    require(process.returncode == 0, 'Image scanner did not complete')
    scan = json.loads(raw.read_bytes())
    shared.retained.shared.empty_filter_markers(scan)
    require(not scan.get('Errors') and scan.get('ArtifactType') == 'container_image',
            'Complete container image scan is required')
    require(assess_secrets(scan)['passed'], 'Every image secret blocks; raw remains private')
    raw.rename(out / 'raw.json')
    created = datetime.fromisoformat(scan['CreatedAt'].replace('Z', '+00:00'))
    require(start <= created <= finish and scan['Trivy'] == {'Version': '0.75.0'},
            'Image scan does not belong to this invocation')
    if args.component == 'prometheus':
        recipes = ROOT / 'deployment/docker'
        legacy = (recipes / 'Dockerfile.prometheus').read_bytes()
        api = b"--header 'Accept: application/octet-stream' https://api.github.com/repos/prometheus/prometheus/releases/assets/662704548"
        release = b'https://github.com/prometheus/prometheus/releases/download/v3.15.1/prometheus-web-ui-3.15.1.tar.gz'
        require(legacy.count(api) == 1 and (recipes / 'Dockerfile.prometheus-direct-20261122').read_bytes()
                == legacy.replace(api, release, 1),
                'Prometheus UI transport must preserve the original source, build and asset checksum')
    facts = capture(args.image, out / 'actual-payload', save_config=True)
    verify_trivy_source(scan, facts['inspection'], {
        **facts, 'runtime_config': shared.runtime_config(facts['inspection']['Config'])})
    rows = {row['path']: row for row in facts['runtime_manifest']}
    targets = TARGETS[args.component]
    expected = {name: rows[name]['sha256'] for name in targets}
    if subprocess.run(['docker', 'image', 'inspect', TOOLCHAIN], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode:
        subprocess.run(['docker', 'pull', TOOLCHAIN], check=True)
    native = capture_native(facts['image_id'], expected, out / 'native')
    graphs = {}
    context_root = ROOT
    if args.component == 'loki':
        from scripts import go_lock_upgrade_20261122 as lock_upgrade

        lock_upgrade.verify_locks(ROOT)
        context_root = lock_upgrade.previous_source(ROOT)
    for target, profile in targets.items():
        receipt, packages = load_current_context(profile, context_root)
        actual = native[target]['native']
        verify_graph(actual, receipt, packages)
        graphs[target] = packages, actual
    go_targets = {r['Target'].lstrip('/') for r in scan['Results'] if r.get('Type') == 'gobinary'}
    require(go_targets == set(targets), 'Complete image Go target coverage differs')
    for result in scan['Results']:
        if result.get('Type') == 'gobinary':
            verify_go_inventory(result, graphs[result['Target'].lstrip('/')][1])
    tempo_proof = None
    if args.component == 'tempo':
        tempo_proof = load_tempo_proof()
        require(expected['tempo'] == tempo_proof['actual_elf_sha256'],
                'Actual Tempo ELF changed beyond the supplementary exact source proof')
        require(receipt['raw_sha256'] == tempo_proof['compiled_files_context']['raw_sha256']
                and receipt['raw_bytes'] == tempo_proof['compiled_files_context']['raw_bytes'],
                'Current Tempo compiled-file context differs from the supplementary source proof')
    milvus_os_scope = None
    if args.component == 'milvus':
        if scan['Metadata']['OS'] == {'Family': 'ubuntu', 'Name': '24.04'}:
            from scripts import check_milvus_noble_20261122 as milvus_os
        else:
            from scripts import check_milvus_os_20261115 as milvus_os
        milvus_os_scope = milvus_os.verify(facts, scan, out / 'actual-payload/rootfs.tar')
    verdict = evaluate_findings(scan, graphs, tempo_proof, milvus_os_scope)
    verdict.update(component=args.component, image_id=facts['image_id'],
                   config_digest=facts['config_digest'], diff_ids=facts['diff_ids'],
                   native_graph_verified=True, all_severities_scanned=True)
    (out / 'verdict.json').write_text(json.dumps(verdict, indent=2) + '\n')
    print(json.dumps(verdict))
    return 0 if verdict['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--component', choices=TARGETS, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--scanner', default='trivy')
    parser.add_argument('--cache-dir', type=Path)
    raise SystemExit(run(parser.parse_args()))
