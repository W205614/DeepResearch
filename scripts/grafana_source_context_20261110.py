"""Recheck existing independent source contexts without assigning them to Grafana."""
from __future__ import annotations

import gzip
import hashlib
from pathlib import Path
import subprocess
import tempfile

from scripts import check_grafana_source_build_20261110 as build
from scripts import check_fs_source_applicability as source

BASE = 'deployment/security/code-scanning-review-20261110-evidence/'
REVIEW = BASE + 'review.json'
CONTINUITY = BASE + 'standalone/source-continuity.json'
STANDALONE = BASE + 'standalone/review.json'
MAINTAINED = BASE + 'telemetry-example/review.json'
OPENPGP = 'GO-2026-5932'
XCRYPTO = 'golang.org/x/crypto'
require = source.require
COMPONENT = {'alertmanager': 'alertmanager', 'amtool': 'alertmanager', 'storage-bootstrap': 'storage-init',
    'etcd': 'etcd', 'etcdctl': 'etcd', 'etcdutl': 'etcd', 'loki': 'loki', 'prometheus': 'prometheus',
    'promtool': 'prometheus', 'tempo': 'tempo', 'otel': 'otel-collector', 'milvus_main': 'milvus', 'milvus_parser': 'milvus'}


def bound(root, profile, name):
    path = build.checked(root, name)
    require(profile['evidence_sha256'].get(name) == build.digest(path), 'Independent source context is not hash-bound')
    return path


def read(root, profile, name):
    return source.json_loads(bound(root, profile, name).read_bytes())


def projection(root, profile, name, expected_sha, expected_raw=None):
    path = bound(root, profile, name)
    require(build.digest(path) == expected_sha, 'Complete public graph projection changed')
    with gzip.open(path, 'rb') as stream:
        raw = stream.read(source.MAX_REPORT_BYTES + 1)
    require(len(raw) <= source.MAX_REPORT_BYTES and (expected_raw is None
            or hashlib.sha256(raw).hexdigest() == expected_raw), 'Complete projection raw identity differs')
    value = source.json_loads(raw)
    rows = value['packages']
    require(isinstance(rows, list) and len(rows) == value['package_count'] > 0,
            'Complete public package-node coverage differs')
    names, imports = set(), set()
    for row in rows:
        name = row['ImportPath']
        require(isinstance(name, str) and name and name not in names
                and not row.get('Error') and not row.get('DepsErrors') and not row.get('Incomplete'),
                'Independent graph has duplicate/error/incomplete package nodes')
        names.add(name)
        for field in ('Imports', 'Deps'):
            values = row.get(field, [])
            require(isinstance(values, list) and all(isinstance(v, str) and v for v in values),
                    'Independent complete package edges are invalid')
            imports.update(values)
        mappings = row.get('ImportMap', {})
        require(isinstance(mappings, dict) and all(isinstance(v, str) and v for v in mappings.values()),
                'Independent complete import mapping is invalid')
        imports.update(mappings.values())
    require(imports - {'C'} <= names, 'Independent graph package/import closure is incomplete')
    return value, rows, names


def continuity(root, profile):
    record = read(root, profile, CONTINUITY)
    for tree in record['source_trees']:
        actual = subprocess.check_output(['git', 'ls-tree', 'HEAD', tree['path']], cwd=root, text=True).strip()
        require(actual.split('\t')[0].split()[-1] == tree['current_git_object'] == tree['baseline_git_object']
                and tree['same_git_object'] is True
                and len(tree['files_sha256']) == tree['tracked_file_count'], 'Independent source subtree changed')
        for name, expected in tree['files_sha256'].items():
            require(build.digest(bound(root, profile, name)) == expected, 'Independent source file changed')


def affected_imports(root, profile):
    advisory = read(root, profile, BASE + 'advisories/GO-2026-5932-current.json')
    require(advisory['id'] == OPENPGP, 'The exact official affected-package advisory is required')
    imports = sorted({i['path'] for a in advisory['affected'] for i in a.get('ecosystem_specific', {}).get('imports', [])})
    require(len(imports) == 7 and all(p == XCRYPTO + '/openpgp' or p.startswith(XCRYPTO + '/openpgp/') for p in imports),
            'Official package applicability scope changed')
    return imports


def exact_previous(root, profile, row, proof):
    require(proof['target'] == row['target'] and proof['source_sha256'] == build.digest(bound(root, profile, row['target'])),
            'Independent proof belongs to another complete source target')
    review = read(root, profile, REVIEW)
    keys = ('id', 'target', 'package', 'version')
    matches = [r for r in review['alerts'] if all(row[k] == r[k] for k in keys)
               and row['severity'] == (r.get('security_severity') or 'UNKNOWN').upper()]
    require(len(matches) == 1 and matches[0]['source_sha256'] == proof['source_sha256'],
            'Exact independent all-severity tuple or source identity differs')
    return review, matches[0]


def external_graph(root, profile, row, proof):
    require(proof['review'] == REVIEW and row['id'] == OPENPGP and row['package'] == XCRYPTO,
            'External proof is limited to the exact independently reviewed OpenPGP advisory')
    review, previous = exact_previous(root, profile, row, proof)
    names = proof['profiles']
    require(names == previous['profiles'] and proof['package_prefixes'] == affected_imports(root, profile),
            'External source/runtime profile or official affected imports differ')
    continuity(root, profile)
    for name in names:
        spec = review['other_complete_graph_profiles'][name]
        component = COMPONENT[name]
        expected = profile['expected']['source_scan']['external_runtime_inputs'][component]
        needed = {'Dockerfile.milvus'} if component == 'milvus' else {'deployment/docker/Dockerfile.' + component}
        if component != 'storage-init':
            needed.add('deployment/vendors/' + component + '/source.lock.json')
        require(needed <= expected.keys(), 'Exact current component recipe and source lock must be reviewed')
        for path, digest in expected.items():
            require(build.digest(bound(root, profile, path)) == digest, 'Selected external runtime recipe changed')
        receipt = read(root, profile, spec['source_receipt'])
        require(receipt['schema'] == 'public-go-graph-identity-receipt-projection-v1'
                and receipt['profile'] == name and receipt['original_completion']['exit_code'] == 0
                and receipt['source_inputs'] == spec['source_inputs']
                and receipt['native'] == spec['native'] and receipt['native_sha256'] == spec['native_sha256']
                and receipt['original_bound_elf_sha256'] == spec['original_bound_elf_sha256']
                and receipt['public_graph_sha256'] == spec['graph_sha256']
                and receipt['public_projection_raw_sha256'] == spec['graph_raw_sha256'],
                'Independent graph/native/source receipt chain differs')
        for path, digest in receipt['source_inputs'].items():
            require(build.digest(bound(root, profile, path)) == digest, 'Actual external graph source lock changed')
        graph, rows, packages = projection(root, profile, spec['graph'], spec['graph_sha256'], spec['graph_raw_sha256'])
        require(graph['schema'] == 'complete-go-package-module-import-projection-v1'
                and graph['profile'] == name and graph['derived_not_original_raw'] is True
                and all(graph[k] is True for k in ('all_package_nodes_preserved',
                    'all_imports_deps_importmaps_preserved', 'all_module_identity_version_sum_replacement_metadata_preserved'))
                and len(packages) == receipt['package_count'], 'Full independent projection coverage differs')
        native = read(root, profile, spec['native'])
        if name.startswith('milvus_'):
            filename = 'milvus' if name == 'milvus_main' else 'libmilvus-planparser.so'
            selected, = [r for r in native['files'] if r['name'] == filename]
            require(selected['sha256'] == spec['original_bound_elf_sha256'], 'Independent Milvus ELF subject differs')
            native = selected['native']
        # Go's JSON build-info omits an empty Value; the retained receipt spells
        # those same empty CGO_* values explicitly. Preserve both raw records.
        settings = {r['Key']: r.get('Value', '') for r in native['Settings']}
        require(len(settings) == len(native['Settings']) and settings == receipt['compiler_settings']
                and native['GoVersion'] == receipt['compiler'], 'Independent native compiler/settings differ')
        # Preserve the original audit's exact local-module/(devel) projection.
        # It compares all selected dependency identities; no raw evidence is rewritten.
        def effective(module):
            value = module.get('Replace', module)
            version = value.get('Version')
            if (value['Path'].startswith(('./', '../')) or name.startswith('milvus_') and module.get('Main')) and version in (None, '(devel)'):
                version = '(devel)'
            return value['Path'], version, value.get('Sum')
        native_paths = {r['Path'] for r in native['Deps']}
        modules = {effective(r['Module']) for r in rows if 'Module' in r and
            (not r['Module'].get('Main') or name.startswith('milvus_') and r['Module']['Path'] in native_paths)}
        require(modules == {effective(r) for r in native['Deps']} and len(modules) == receipt['module_count'],
                'Complete independent graph/native dependency identities differ')
        require(not any(p == prefix or p.startswith(prefix + '/') for p in packages for prefix in proof['package_prefixes']),
                'An officially affected package exists in an independent full graph')


def standalone_context(root, profile, row, proof):
    require(proof['review'] == STANDALONE, 'Only the exact four independently checked standalone contexts are supported')
    exact_previous(root, profile, row, proof)
    review = read(root, profile, STANDALONE)
    spec, = [r for r in review['rows'] if r['alert'] == proof['alert'] and r['target'] == row['target']]
    require(spec['target_sha256'] == proof['source_sha256'] and spec['exit_code'] == 0
            and spec['graph_error_count'] == 0 and spec['source_versions_and_replacements_unchanged'] is True
            and spec['command'][:6] == ['go', 'list', '-deps', '-test', '-json', '-mod=readonly'],
            'Standalone source/test context or original completion differs')
    require(build.digest(bound(root, profile, str(Path(row['target']).with_name('go.sum')).replace('\\', '/')))
            == spec['original_go_sum_sha256'], 'Standalone original checksum snapshot changed')
    component = 'etcd' if row['target'].startswith('deployment/vendors/etcd/') else 'milvus'
    lock_name = 'deployment/vendors/' + component + '/source.lock.json'
    require(build.digest(bound(root, profile, lock_name)) == spec['source_lock_sha256'],
            'Standalone original upstream source lock changed')
    lock = read(root, profile, lock_name)
    archive_sha = lock['source_archive_sha256'] if component == 'etcd' else lock['source']['source.json']['sha256']
    require(archive_sha == spec['source_archive_sha256'], 'Standalone original upstream source archive identity differs')
    continuity(root, profile)
    projection_spec = spec['complete_public_projection']
    graph, _, names = projection(root, profile, BASE + 'standalone/' + projection_spec['file'],
                                projection_spec['sha256'], projection_spec['raw_sha256'])
    require(graph['schema'] == 'complete-go-package-projection-v1' and len(names) == spec['graph_packages'],
            'Standalone complete package/test coverage differs')
    if row['id'] == OPENPGP and row['package'] == XCRYPTO:
        expected = affected_imports(root, profile)
    else:
        require(row['target'] == 'deployment/vendors/etcd/modules/tools/mod/go.mod'
                and row['id'] == 'CVE-2026-2303' and row['package'] == 'go.mongodb.org/mongo-driver'
                and row['severity'] == 'MEDIUM', 'Unreviewed standalone finding cannot inherit the Mongo proof')
        advisory = read(root, profile, BASE + 'advisories/CVE-2026-2303-current-cna.json')
        require(advisory['cveMetadata']['cveId'] == row['id']
                and sorted(p for p in names if p.startswith('go.mongodb.org/mongo-driver/')) == spec['mongo_packages']
                and spec['mongo_packages'], 'Actual imported Mongo BSON subset differs from the independent source review')
        expected = ['go.mongodb.org/mongo-driver/x/mongo/driver/auth/internal/gssapi']
    require(proof['package_prefixes'] == expected
            and not any(p == prefix or p.startswith(prefix + '/') for p in names for prefix in expected),
            'An affected package is imported in the actual standalone source/test context')


def maintained_patch(root, profile, row, proof):
    require(proof['review'] == MAINTAINED and row['target'] == 'deployment/vendors/milvus/modules/examples/telemetry_demo/go.mod'
            and row['id'] == OPENPGP and row['package'] == XCRYPTO
            and row['disposition'] == 'MAINTAINED_REPLACEMENT_VERIFIED',
            'Maintained-example proof cannot declare the preserved original snapshot fixed')
    exact_previous(root, profile, row, proof)
    review = read(root, profile, MAINTAINED)
    require(review['status'] == 'passed' and review['alert'] == 35 and review['derived_public_receipt_not_original'] is True,
            'The actual maintained-example review is incomplete')
    lock = read(root, profile, 'deployment/vendors/milvus/source.lock.json')['source']['source.json']
    require(lock['sha256'] == review['source_archive_sha256'] and lock['commit'] == review['source_commit'],
            'Maintained example no longer belongs to the exact upstream source')
    patch = bound(root, profile, BASE + 'telemetry-example/' + review['patch']['file'])
    require(build.digest(patch) == review['patch']['sha256'], 'Exact maintained patch changed')
    with tempfile.TemporaryDirectory(prefix='dr-source-example-') as temporary:
        directory = Path(temporary)
        for name, expected in review['original_files'].items():
            source_name = ('tests/fixtures/milvus-telemetry-example-20261110/' + name + '.txt' if name.endswith('.go')
                           else str(Path(row['target']).with_name(name)).replace('\\', '/'))
            path = bound(root, profile, source_name)
            require(build.digest(path) == expected, 'Original example source snapshot changed')
            (directory / name).write_bytes(path.read_bytes())
        for extra in (['--check'], []):
            subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', *extra, str(patch)], cwd=directory,
                           check=True, capture_output=True)
        require(all(build.digest(directory / name) == expected for name, expected in review['maintained_files'].items()),
                'Actual maintained patch output differs')
    for name, expected in review['local_client_pkg_source']['locks_sha256'].items():
        require(build.digest(bound(root, profile, name)) == expected, 'Maintained client/pkg source locks changed')
    graph_spec = review['package_graph']
    _, _, names = projection(root, profile, BASE + 'telemetry-example/' + graph_spec['file'], graph_spec['public_derived_sha256'])
    require(graph_spec['exit_code'] == 0 and len(names) == graph_spec['packages']
            and not any(p == prefix or p.startswith(prefix + '/') for p in names for prefix in affected_imports(root, profile)),
            'Maintained complete package/test graph includes affected imports')
    modules_spec = review['full_module_graph']['public_projection']
    modules_name = BASE + 'telemetry-example/' + modules_spec['file']
    require(build.digest(bound(root, profile, modules_name)) == modules_spec['sha256'], 'Maintained MVS projection changed')
    modules = read(root, profile, modules_name)['modules']
    crypto, = [r for r in modules if r['Path'] == XCRYPTO]
    official, = [r for r in review['official_modules'] if r['path'] == XCRYPTO]
    require(len(modules) == review['full_module_graph']['selected_modules']
            and crypto['Version'] == official['version'] == 'v0.58.1-0.20261009181718-2f1f834330b2'
            and crypto['Sum'] == official['sum'] and crypto['GoModSum'] == official['go_mod_sum']
            and not crypto.get('Replace') and all(r['exit_code'] == 0 for r in review['checks']),
            'Actual selected maintained dependency is not the official fixed MVS node')
    checks = {r['step']: r for r in review['checks']}
    for name, binary in review['binaries'].items():
        stem = name.removesuffix('.go')
        require(checks['build-' + stem]['command'] == ['go', 'build', '-mod=readonly', '-o', binary['file'], name]
                and checks['test-compile-' + stem]['command'] == ['go', 'test', '-mod=readonly', '-run', '^$', name]
                and binary['platform'] == 'linux/amd64 ELF' and build.HEX.fullmatch(binary['sha256'])
                and binary['bytes'] > 0, 'Actual three maintained compilation receipts are incomplete')
    return {'original_snapshot_fixed': False, 'maintained_replacement_verified': True,
            'new_application_execution_claimed': False}
