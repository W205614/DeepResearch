"""Bind the current source inventories while retaining immutable historical proofs."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import gzip
import json

from scripts import check_grafana_source_scan_20261110 as prior
from scripts import check_grafana_source_build_20261110 as build
from scripts import check_grafana_source_build_20261114 as current

REVIEW = 'deployment/security/source-dependency-migration-20261115.json'
REVIEW_SHA256 = 'e174943a37081bb4da6b596f83ae35b5a83d6d65017c0bfa2cec3d416a911692'
CONTEXTS = 'deployment/security/source-contexts-20261115/contexts.json'
MINIO_TARGET = 'deployment/vendors/minio-otel-20261115/go.mod'
MINIO_FINDINGS = frozenset({
    ('GO-2026-5932', MINIO_TARGET, 'golang.org/x/crypto', 'v0.58.0', 'UNKNOWN'),
    ('CVE-2026-78661', MINIO_TARGET, 'golang.org/x/net', 'v0.61.0', 'HIGH'),
    ('CVE-2026-97045', MINIO_TARGET, 'golang.org/x/net', 'v0.61.0', 'MEDIUM'),
})
CONTEXTS_SHA256 = '7e7f9090fc6644f761d2e391dd7f80dd806e8edd52e3a135a76e7299f6dece68'


def checked_review(root):
    path = build.checked(root, REVIEW)
    current.require(current.digest(path) == REVIEW_SHA256, 'Source dependency migration review changed')
    review = json.loads(path.read_bytes())
    current.require(review['schema'] == 'source-dependency-migration-20261115-v1',
                    'Unsupported source dependency migration')
    for name, expected in review['source_inputs'].items():
        current.require(current.digest(build.checked(root, name)) == expected,
                        'Reviewed dependency input changed: ' + name)
    return review


def verify_observed(observed, review):
    current.require(observed['scan_targets'] == review['scan_targets']
                    and observed['target_inventory_sha256'] == review['target_inventory_sha256'],
                    'Complete migrated source inventory differs')
    reviewed = Counter(map(build.finding_key, review['findings']))
    current.require(all(value == 1 for value in reviewed.values())
                    and Counter(map(build.finding_key, observed['findings'])) == reviewed
                    and len(observed['findings']) == len(review['findings']),
                    'New, missing, duplicate or reclassified source findings remain blocked')


def load_current_context(profile_name, root=build.ROOT):
    index_path = build.checked(root, CONTEXTS)
    current.require(build.digest(index_path) == CONTEXTS_SHA256,
                    'Current source context index changed')
    index = prior.old.json_loads(index_path.read_bytes())
    current.require(index['schema'] == 'current-source-context-index-20261115-v1'
                    and profile_name in index['contexts'], 'Unreviewed current source context')
    spec = index['contexts'][profile_name]
    receipt_path, graph_path = [build.checked(root, spec[name]) for name in ('receipt', 'graph')]
    current.require(build.digest(receipt_path) == spec['receipt_sha256']
                    and build.digest(graph_path) == spec['graph_sha256'], 'Current source graph or receipt changed')
    receipt = prior.old.json_loads(receipt_path.read_bytes())
    command = receipt['command']
    current.require(command[:2] == ['go', 'list'] and '-deps' in command and '-json' in command
                    and [value for value in command if value.startswith('-mod=')] == ['-mod=readonly']
                    and '-e' not in command, 'Current graph was not collected by the complete readonly command')
    expected_tags = {'loki': 'netgo', 'prometheus': 'netgo,builtinassets', 'promtool': 'netgo,builtinassets',
                     'otel': 'grpcnotrace', 'minio_silo': 'kqueue', 'milvus_main': 'dynamic,sonic,with_jemalloc,bytedance_tango'}.get(profile_name)
    current.require([value.removeprefix('-tags=') for value in command if value.startswith('-tags=')]
                    == ([] if expected_tags is None else [expected_tags]), 'Current graph build tags differ')
    if profile_name.startswith(('standalone-', 'maintained-')):
        current.require('-test' in command, 'The standalone source/test context is incomplete')
    current.require(receipt['profile'] == profile_name and receipt['exit_code'] == 0
                    and receipt['raw_bytes'] > 0 and build.HEX.fullmatch(receipt['raw_sha256'])
                    and receipt['projection_sha256'] == spec['graph_sha256']
                    and receipt['raw_graph_modified'] is False
                    and receipt['projection_preserves_all_packages_imports_and_module_identities'] is True,
                    'Complete successful current source graph receipt is missing')
    current.require(receipt['source_inputs'], 'Current graph has no source binding')
    for name, expected in receipt['source_inputs'].items():
        current.require(build.digest(build.checked(root, name)) == expected,
                        'Current source graph input changed: ' + name)
    copied_directory = ('deployment/vendors/milvus/modules' if profile_name.startswith(('milvus_', 'standalone-48'))
                        else 'deployment/vendors/etcd/modules' if profile_name in {'etcd', 'etcdctl', 'etcdutl', 'standalone-14', 'standalone-22', 'standalone-23'}
                        else 'deployment/vendors/otel-collector/source' if profile_name == 'otel'
                        else 'deployment/vendors/loki/push' if profile_name == 'loki' else None)
    directories = ([copied_directory] if copied_directory is not None else
                   ['deployment/vendors/milvus/modules/client', 'deployment/vendors/milvus/modules/pkg']
                   if profile_name.startswith('maintained-') else [])
    for copied_directory in directories:
        expected_files = {path.relative_to(root).as_posix() for path in (root / copied_directory).rglob('*')
                          if path.is_file() and (path.suffix == '.go' or path.name in {'go.mod', 'go.sum'})}
        current.require(expected_files <= receipt['source_inputs'].keys(),
                        'A current copied source file is outside the graph input closure')
    environment = receipt['environment']
    current.require(environment['GOOS'] == 'linux' and environment['GOARCH'] == 'amd64'
                    and environment['GOAMD64'] == 'v1'
                    and environment['GOWORK'] == ('' if profile_name.startswith('minio_') else 'off')
                    and environment['CGO_ENABLED'] == ('1' if profile_name.startswith('milvus_') else '0'),
                    'Current graph target environment differs')
    version = 'go1.26.11' if profile_name.startswith(('milvus_', 'standalone-48', 'maintained-')) else 'go1.27.4'
    current.require(receipt['compiler'].startswith('go version ' + version + ' '),
                    'Current graph uses another compiler')
    with gzip.open(graph_path, 'rb') as stream:
        raw = stream.read(prior.old.MAX_REPORT_BYTES + 1)
    current.require(len(raw) <= prior.old.MAX_REPORT_BYTES
                    and hashlib.sha256(raw).hexdigest() == receipt['projection_raw_sha256'],
                    'Current complete graph projection differs')
    graph = prior.old.json_loads(raw)
    current.require(graph['schema'] == 'current-source-context-20261115-v1'
                    and isinstance(graph['packages'], list)
                    and graph['package_count'] == len(graph['packages']) == receipt['package_count'] > 0,
                    'Current source graph package coverage differs')
    packages = {}
    for row in graph['packages']:
        name = row['ImportPath']
        current.require(isinstance(name, str) and name and name not in packages
                        and not row.get('Error') and not row.get('DepsErrors') and not row.get('Incomplete'),
                        'Current source graph has duplicate, missing or incomplete packages')
        packages[name] = row
    special = {'C'} if environment['CGO_ENABLED'] == '1' else set()
    for row in packages.values():
        for field in ('Imports', 'Deps'):
            values = row.get(field, [])
            current.require(isinstance(values, list) and all(isinstance(v, str) and v for v in values)
                            and set(values) - special <= packages.keys(), 'Current graph package closure is incomplete')
        mappings = row.get('ImportMap', {})
        current.require(isinstance(mappings, dict) and set(mappings.values()) - special <= packages.keys(),
                        'Current graph import mapping closure is incomplete')
    return receipt, packages


def verify_minio_findings(root, findings, review):
    from scripts import check_minio_otel_20261115 as minio
    current.require(Counter(map(build.finding_key, findings)) == Counter(MINIO_FINDINGS),
                    'Additional MinIO findings exceed the three exact source tuples')
    profile = minio.load_profile(root)
    advisories = {}
    for identifier, spec in review['minio_source_advisories'].items():
        path = build.checked(root, spec['path'])
        current.require(build.digest(path) == spec['sha256'], 'MinIO official source advisory changed')
        advisory = prior.old.json_loads(path.read_bytes())
        current.require(identifier == advisory['id'] or identifier in advisory.get('aliases', []),
                        'MinIO official source advisory has another identity')
        advisories[identifier] = advisory
    verified = []
    for binary in ('silo', 'mcli'):
        receipt, graph = load_current_context('minio_' + binary, root)
        current.require(receipt['source_provenance']['source_profile_sha256']
                        == build.digest(build.checked(root, minio.PROFILE_PATH)),
                        'MinIO production graph belongs to another source subject')
        native = prior.old.json_loads(build.checked(root,
            'deployment/security/minio-otel-source-20261115-evidence/' + binary + '-native.json').read_bytes())
        current.require(minio.prior.shared.manifest_hash(native) == profile['native_sha256'][binary],
                        'MinIO actual native metadata differs from its verified subject')
        minio.verify_graph_native(graph, native)
        modules = minio.prior.common.native_modules(native)
        for row in findings:
            module = modules[row['package']]
            current.require(module['Version'] == row['version'] and not module.get('Replace'),
                            'MinIO affected library identity differs; no inherited absence proof')
            advisory = advisories[row['id']]
            affected = [value for value in advisory['affected'] if value['package']['name'] == row['package']]
            current.require(len(affected) == 1, 'MinIO module advisory scope is ambiguous')
            imports = {value['path'] for value in affected[0]['ecosystem_specific']['imports']}
            expected = ({'golang.org/x/crypto/openpgp' + suffix for suffix in
                         ('', '/packet', '/armor', '/clearsign', '/errors', '/elgamal', '/s2k')}
                        if row['package'] == 'golang.org/x/crypto' else {'golang.org/x/net/html'})
            current.require(imports == expected
                            and not any(name == prefix or name.startswith(prefix + '/')
                                        for name in graph for prefix in imports),
                            'An officially affected MinIO package is compiled into the actual entry closure')
        verified.append({'context': 'minio_' + binary, 'packages': len(graph),
                         'current_affected_html_or_openpgp_packages': [], 'libraries_upgraded_claimed': False})
    return verified


def verify_current_proofs(root, historical, profile):
    from scripts import go_otel_rebuild_20261114 as otel
    verified = []
    contexts = {}
    grafana = {}
    for row in historical['expected']['source_scan']['reviewed_findings']:
        proof = row['proof']
        kind = proof['kind']
        if kind in {'compiled_absence', 'compiled_dependency'}:
            name = proof['profile']
            if name not in grafana:
                target = next(path for path in profile['native_evidence']
                              if path == build.MAIN and name == 'main'
                              or '/plugins-bundled/' + name + '/' in path)
                evidence = profile['native_evidence'][target]
                native = json.loads(build.checked(root, evidence['native']).read_bytes())
                graph = otel.decode_graph(gzip.decompress(build.checked(root, evidence['graph']).read_bytes()))
                grafana[name] = otel.native_modules(native), graph
            native, graph = grafana[name]
            if kind == 'compiled_dependency':
                current.require(native[proof['module']] == proof['expected_identity'],
                                'Original fixed dependency changed in the current Grafana executable')
            else:
                current.require(not any(p == prefix or p.startswith(prefix + '/') for p in graph
                                        for prefix in proof['package_prefixes']),
                                'Original affected package is compiled in current Grafana')
        elif kind in {'external_graph', 'standalone_context', 'maintained_patch'}:
            names = (proof['profiles'] if kind == 'external_graph' else
                     ['standalone-' + str(proof['alert'])] if kind == 'standalone_context' else
                     ['maintained-telemetry'])
            for name in names:
                if name not in contexts:
                    contexts[name] = load_current_context(name, root)
                receipt, packages = contexts[name]
                prefixes = proof.get('package_prefixes', ['golang.org/x/crypto/openpgp'])
                current.require(not any(p == prefix or p.startswith(prefix + '/') for p in packages for prefix in prefixes),
                                'An affected package is imported by the current source context')
                if kind == 'maintained_patch':
                    review_path = build.checked(root, proof['review'])
                    current.require(build.digest(review_path) == historical['evidence_sha256'][proof['review']],
                                    'The immutable maintained-example review changed')
                    original = prior.old.json_loads(review_path.read_bytes())
                    provenance = receipt['source_provenance']
                    current.require(provenance['maintained_go_source_sha256']
                                    == {p: h for p, h in original['maintained_files'].items() if p.endswith('.go')}
                                    and provenance['applied_go_only_patch_sha256'] == original['patch']['sha256'],
                                    'The maintained example source differs from its reviewed import repair')
                    patch = provenance['current_maintained_patch']
                    current.require(receipt['source_inputs'].get(patch['path']) == patch['sha256']
                                    and patch['applies_to_current_original_snapshots'] is True
                                    and patch['exact_output_matches_actual_current_graph_and_three_built_examples'] is True,
                                    'The maintained replacement is not reproducible from current original snapshots')
        else:
            raise ValueError('No current migration for source proof kind: ' + kind)
        verified.append({**{k: row[k] for k in ('id', 'target', 'package', 'version', 'severity')},
                         'current_proof_kind': kind})
    return verified


def collect(scan, receipt, original, candidate, *, raw_sha256, original_sha256,
            source_commit, tracked_clean, historical, historical_root, root):
    review = checked_review(root)
    current.require(review['contexts_sha256'] == CONTEXTS_SHA256, 'Source finding and context reviews differ')
    observed, counts, findings = prior.source_facts(scan)
    verify_observed(observed, review)
    now = datetime.now(timezone.utc)
    _, scope, _ = prior.old.load_scope(root, prior.old.POLICY, now)
    scope = deepcopy(scope)
    target = next(r for r in scan['Results'] if r['Target'] == scope['source_path'])
    packages = prior.old.inventory(target['Packages'])
    scope['source_inventory_count'] = len(packages)
    scope['source_inventory_sha256'] = hashlib.sha256(prior.old.canonical(packages)).hexdigest()
    prior.old.validate_completion(scan, receipt, raw_sha256, scope, source_commit,
        tracked_clean, findings, now,
        expected_targets=prior.old.committed_manifest_targets(root, source_commit))
    recomputed = prior.old.evaluate(scan, receipt, raw_sha256=raw_sha256,
        source_commit=source_commit, tracked_clean=tracked_clean, root=root, now=now)
    current.require(recomputed == original, 'Original failed verdict differs from recomputation')
    recomputed_candidate = prior.candidate.evaluate(scan, receipt, original,
        raw_sha256=raw_sha256, source_commit=source_commit, tracked_clean=tracked_clean,
        root=root, now=now)
    current.require({k: v for k, v in candidate.items()
                     if k != 'preserved_original_verdict_file_sha256'} == recomputed_candidate
                    and candidate['preserved_original_verdict_file_sha256'] == original_sha256,
                    'Candidate failed verdict differs from recomputation')
    compiled = prior.verify_compiled(historical_root, historical)
    legacy = historical['expected']['source_scan']
    retained_keys = set(map(build.finding_key, legacy['reviewed_findings']))
    actual_keys = set(map(build.finding_key, observed['findings']))
    current.require(retained_keys <= actual_keys, 'An original reviewed finding disappeared or changed')
    retained = {'scan_targets': legacy['scan_targets'],
                'target_inventory_sha256': legacy['target_inventory_sha256'],
                'findings': legacy['reviewed_findings']}
    # This separate projection validates the old immutable compilation; the raw
    # current scan and its full inventories above are never rewritten.
    prior.assess_findings(historical_root, historical, retained, compiled)
    prior.verify_busybox(historical_root, historical)
    new = [row for row in observed['findings'] if build.finding_key(row) not in retained_keys]
    minio_rows = [row for row in new if row['target'] == MINIO_TARGET]
    new = [row for row in new if row['target'] != MINIO_TARGET]
    current.require(new == review['additional_historical_otel_findings']
                    and minio_rows == review['additional_minio_findings'],
                    'Additional source findings exceed the exact reviewed migration scope')
    minio_proofs = verify_minio_findings(root, minio_rows, review)
    profile = current.load_profile(root)
    current_proofs = verify_current_proofs(root, historical, profile)
    for row in new:
        current.require(row['id'] == 'CVE-2026-128085' and row['severity'] == 'MEDIUM'
                        and row['target'].startswith('deployment/security/')
                        and row['package'].startswith('go.opentelemetry.io/otel/'),
                        'Only the retained historical otel findings have a migration proof')
        target_name = 'main'
        if '/plugins/' in row['target']:
            target_name = row['target'].split('/plugins/', 1)[1].split('/', 1)[0]
        paths = [name for name in profile['native_evidence']
                 if name == build.MAIN and target_name == 'main'
                 or '/plugins-bundled/' + target_name + '/' in name]
        current.require(len(paths) == 1, 'Current executable migration fact is ambiguous')
        evidence = profile['native_evidence'][paths[0]]
        native = json.loads(build.checked(root, evidence['native']).read_bytes())
        modules = {m['Path']: m for m in [native['Main'], *native.get('Deps', [])]}
        module = modules.get(row['package'])
        current.require(module is None or module['Version'] == 'v1.47.0',
                        'Affected otel module remains in the current executable')
    return {'passed': True, 'mode': 'exact_source_dependency_migration_verified',
            'unresolved_count': 0, 'raw_counts': counts,
            'verified_all_severity_findings': len(observed['findings']),
            'additional_historical_otel_findings': new,
            'current_retained_proofs': current_proofs,
            'additional_minio_findings': minio_rows, 'current_minio_source_proofs': minio_proofs,
            'raw_report_modified': False, 'historical_compilation_is_current': False,
            'source_dependency_review': REVIEW, 'source_dependency_review_sha256': REVIEW_SHA256,
            'preserved_original_failed_verdict': original,
            'preserved_candidate_failed_verdict': candidate,
            'binary_redistribution_approved': False}
