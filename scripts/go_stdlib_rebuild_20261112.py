"""Compare actual Go security rebuilds with their exact prior native/source facts."""
from __future__ import annotations

import json
import gzip

from scripts import check_grafana_source_build_20261110 as shared
from scripts.retained_source_review_20261112 import SOURCE, snapshot

TOOLCHAIN = 'golang:1.27.4-alpine@sha256:0a5f7fe796599ff487605987432e7619ae45beeb2fba4c2ce0b907879932aebf'
ADVISORY, CVE, TARGET_VERSION = 'GO-2026-6859', 'CVE-2026-97054', 'go1.27.4'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def native_modules(native):
    modules = {}
    for row in [native['Main'], *native.get('Deps', [])]:
        path = row['Path']
        require(path not in modules, 'Duplicate native module identity')
        modules[path] = {key: row[key] for key in ('Path', 'Version', 'Sum', 'Replace') if key in row}
    return modules


def compare_native(current, previous):
    require(current.get('GoVersion') == TARGET_VERSION, 'Actual ELF was not compiled with fixed Go 1.27.4')
    require(previous.get('GoVersion') == 'go1.27.3', 'Prior exact compiler identity differs')
    require(current.get('Path') == previous.get('Path'), 'Actual native entry package changed')
    require(native_modules(current) == native_modules(previous),
            'Complete actual non-stdlib module versions, sums or replacements changed')
    settings = {row['Key']: row['Value'] for row in current.get('Settings', [])}
    old_settings = {row['Key']: row['Value'] for row in previous.get('Settings', [])}
    for key in ('CGO_ENABLED', 'GOOS', 'GOARCH', 'GOAMD64', '-tags', 'vcs.revision', 'vcs.modified'):
        require(settings.get(key) == old_settings.get(key), 'Native build setting changed: ' + key)
    require(all(settings.get(key) == value for key, value in
                {'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': 'amd64', 'GOAMD64': 'v1'}.items()),
            'Fixed build is outside the verified Linux/amd64 production profile')
    return {'go_version': TARGET_VERSION, 'entry_package': current['Path'],
            'module_count': len(native_modules(current)),
            'nonstdlib_modules_sha256': shared.manifest_hash(native_modules(current)),
            'nonstdlib_modules_identical': True, 'source_and_build_settings_preserved': True}


def decode_graph(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Duplicate production graph field')
            value[key] = item
        return value
    decoder, cursor, packages = json.JSONDecoder(object_pairs_hook=unique), 0, {}
    text = raw.decode('utf-8')
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor == len(text):
            break
        row, cursor = decoder.raw_decode(text, cursor)
        require(isinstance(row, dict) and not row.get('Error') and not row.get('DepsErrors')
                and not row.get('Incomplete'), 'Production dependency graph is incomplete')
        name = row['ImportPath']
        require(name not in packages, 'Duplicate production dependency package')
        packages[name] = row
    require(packages and all(set(row.get('Imports', [])) <= packages.keys() for row in packages.values()),
            'Production package import closure is incomplete')
    return packages


def compare_graphs(current_raw, previous_raw):
    current, previous = decode_graph(current_raw), decode_graph(previous_raw)
    def identity(module):
        # Dir and GoMod are transport locations (the prior graph ran on Windows).
        # Preserve immutable module/checksum identity, including local replacement paths.
        value = {key: module.get(key) for key in
                 ('Path', 'Version', 'Sum', 'GoModSum', 'Main', 'GoVersion', 'Time')}
        if 'Replace' in module:
            value['Replace'] = identity(module['Replace'])
        return value
    def nonstdlib(packages):
        return {name: {'imports': sorted(row.get('Imports', [])),
                       'module': identity(row.get('Module', {}))}
                for name, row in packages.items() if not row.get('Standard')}
    actual, old = nonstdlib(current), nonstdlib(previous)
    require(actual == old, 'Production non-stdlib package/module/import graph changed')
    return {'complete_package_count': len(current), 'nonstdlib_package_count': len(actual),
            'nonstdlib_graph_sha256': shared.manifest_hash(actual),
            'nonstdlib_graph_identical': True, 'complete_import_closure': True}


def check_advisory(advisory):
    require(advisory.get('id') == ADVISORY and advisory.get('aliases') == [CVE]
            and len(advisory.get('affected', [])) == 1, 'Official Go advisory identity changed')
    affected = advisory['affected'][0]
    require(affected['package'] == {'name': 'stdlib', 'ecosystem': 'Go'}
            and affected['ranges'] == [{'type': 'SEMVER', 'events': [
                {'introduced': '0'}, {'fixed': '1.26.11'},
                {'introduced': '1.27.0-0'}, {'fixed': '1.27.4'}]}],
            'Official affected or fixed Go version range changed')
    require({row['path'] for row in affected['ecosystem_specific']['imports']} == {'net/http'}
            and {'type': 'FIX', 'url': 'https://go.dev/cl/862185'} in advisory['references'],
            'Official package scope or fix reference changed')
    return {'advisory': ADVISORY, 'cve': CVE, 'fixed_version': TARGET_VERSION,
            'official_fix': 'https://go.dev/cl/862185'}


def reviewed_runtime_config_equivalent(actual, expected):
    # Docker's inspect/export paths represent an unspecified User as absent,
    # null or "". Keep every explicit user and all other settings exact.
    actual_user, expected_user = actual.get('User'), expected.get('User')
    return ({key: value for key, value in actual.items() if key != 'User'}
            == {key: value for key, value in expected.items() if key != 'User'}
            and (actual_user == expected_user
                 or actual_user in (None, '') and expected_user in (None, '')))


def verify_receipt(receipt, inspection, profile, artifact, *, root=shared.ROOT):
    require(receipt.get('schema') == 'go-stdlib-source-build-receipt-20261112-v1'
            and receipt.get('profile') == artifact + '-source-20261112'
            and receipt.get('image') == 'deepresearch-' + artifact + '-source:20261112'
            and receipt.get('source_inputs') == profile['source_inputs']
            and receipt.get('image_id') == inspection.get('Id')
            and receipt.get('diff_ids') == inspection.get('RootFS', {}).get('Layers')
            and inspection.get('Os') == 'linux' and inspection.get('Architecture') == 'amd64',
            'Actual image/source/platform differs from the new rebuild receipt')
    for name in ('native_sha256', 'runtime_manifest_sha256'):
        require(receipt.get(name) == profile['expected'][name], 'New build receipt differs: ' + name)
    require(isinstance(receipt.get('runtime_config'), dict)
            and reviewed_runtime_config_equivalent(receipt['runtime_config'], profile['expected']['runtime_config']),
            'New build receipt differs: runtime_config')
    require(shared.runtime_config(inspection['Config']) == receipt['runtime_config']
            and isinstance(receipt.get('config_digest'), str)
            and shared.retained.DIGEST.fullmatch(receipt['config_digest']), 'Actual runtime config differs')
    require(inspection['Config'].get('Labels', {}).get('org.deepresearch.project.inputs_sha256')
            == shared.manifest_hash(receipt['source_inputs']),
            'Actual image build-input label differs; current source cannot be assigned to an older image')
    for name, expected in receipt['source_inputs'].items():
        require(shared.digest(shared.checked(root, name)) == expected, 'Current build source input changed')
    return receipt


def review_subject(profile):
    reviews = set(profile['independent_reviews'])
    return shared.manifest_hash({key: value for key, value in profile.items()
        if key not in ('independent_reviews', 'review_subject_sha256', 'evidence_sha256', 'status')}
        | {'evidence_sha256': {name: value for name, value in profile['evidence_sha256'].items()
                              if name not in reviews}})


def load_profile(artifact, root=shared.ROOT):
    path = 'deployment/security/' + artifact + '-source-20261112-profile.json'
    profile = shared.read(shared.checked(root, path))
    require(profile.get('schema') == 'go-stdlib-source-profile-20261112-v1'
            and profile.get('status') == 'verified' and profile.get('profile') == artifact + '-source-20261112'
            and profile.get('image') == 'deepresearch-' + artifact + '-source:20261112'
            and profile.get('dockerfile') == 'deployment/docker/Dockerfile.' + artifact + '-20261112',
            'New exact source-build review is not verified')
    require(profile.get('old_binary_approval_inherited') is False
            and profile.get('binary_redistribution_approved') is False
            and profile.get('historical_review_source') == SOURCE,
            'Historical source facts cannot become new binary redistribution approval')
    sources, evidence = profile['source_inputs'], profile['evidence_sha256']
    require(all(sources[name] == evidence[name] for name in sources.keys() & evidence.keys()),
            'Build source and evidence hashes disagree')
    for name, expected in (sources | evidence).items():
        require(isinstance(expected, str) and shared.HEX.fullmatch(expected)
                and shared.digest(shared.checked(root, name)) == expected, 'Reviewed evidence or source changed')
    require(profile['review_subject_sha256'] == review_subject(profile), 'Review subject differs from actual profile')
    reviews = [shared.read(shared.checked(root, name)) for name in profile['independent_reviews']]
    require(set(profile['independent_reviews']) <= evidence.keys() and len(reviews) >= 2
            and len({row['actor'] for row in reviews}) == len(reviews)
            and all(row.get('facts_passed') is True and row.get('risk_acceptance') is False
                    and row.get('actor_kind') == 'automated_independent_agent'
                    and row.get('binary_redistribution_approved') is False
                    and row.get('subject_sha256') == profile['review_subject_sha256'] for row in reviews),
            'Independent exact technical reviews are incomplete')
    return profile


def verify_migrations(profile, artifact, root=shared.ROOT):
    if artifact == 'grafana':
        prior_profile = shared.load_profile(snapshot(root))
        previous = prior_profile['expected']['native_sha256']
        canonical = {shared.MAIN: {
            'prior_native': prior_profile['expected']['source_scan']['main']['native'],
            'prior_graph': prior_profile['expected']['source_scan']['main']['graph']}}
        for row in shared.read(shared.checked(root, 'deployment/vendors/grafana-20261107/plugin-modules.json')):
            plugin = row['plugin_directory']
            target = 'usr/share/grafana/data/plugins-bundled/' + plugin + '/' + row['binary']
            canonical[target] = {
                'prior_native': 'deployment/security/grafana-fixed-20261107-evidence/native/' + plugin + '-native-buildinfo.json',
                'prior_graph': 'deployment/security/grafana-fixed-20261107-evidence/native/build-evidence/graphs/plugins/' + plugin + '.json.gz'}
    else:
        from scripts.image_disposition import load_binding
        previous = load_binding(root)['binary_sha256']
        canonical = {target: {
            'prior_native': 'deployment/security/minio-source-20261112-evidence/' + target.rsplit('/', 1)[-1] + '-prior-native.json',
            'prior_graph': 'deployment/security/minio-html4-evidence/' + target.rsplit('/', 1)[-1] + '-production-graph-complete.json.gz'}
            for target in previous}
    require(set(profile['native_evidence']) == set(previous)
            and set(profile['expected']['native_sha256']) == set(previous), 'Complete rebuilt native target set differs')
    require(check_advisory(shared.read(shared.checked(root,
                'deployment/security/go-stdlib-20261112-evidence/GO-2026-6859.json')))
            == profile['stdlib_fix'], 'Official fixed-version statement differs')
    facts = {}
    for target, row in profile['native_evidence'].items():
        require(all(row[name] == path for name, path in canonical[target].items()),
                'Prior native/source facts do not belong to this exact target')
        if artifact == 'grafana':
            require(all(shared.digest(shared.checked(root, path)) == prior_profile['evidence_sha256'][path]
                        for path in canonical[target].values()),
                    'Historical compiled facts differ from the actual immutable review')
        require(row['prior_elf_sha256'] == previous[target]
                and row['elf_sha256'] == profile['expected']['native_sha256'][target]
                and row['elf_sha256'] != previous[target], 'Actual rebuilt/prior ELF identity differs')
        require(all(row[name] in profile['evidence_sha256'] for name in
                    ('native', 'prior_native', 'graph', 'prior_graph')),
                'Every complete native/graph input must be bound by the review')
        native = compare_native(shared.read(shared.checked(root, row['native'])),
                                shared.read(shared.checked(root, row['prior_native'])))
        if artifact == 'minio':
            inventory = json.loads(gzip.decompress(shared.checked(root,
                'deployment/security/minio-html4-evidence/actual-native-module-inventory-complete.json.gz').read_bytes()))
            def lower(module):
                value = {'path': module['Path'], 'version': module.get('Version'), 'sum': module.get('Sum')}
                if 'Replace' in module:
                    value['replace'] = lower(module['Replace'])
                return value
            prior_native = native_modules(shared.read(shared.checked(root, row['prior_native'])))
            require({name: lower(module) for name, module in prior_native.items()} == inventory[target],
                    'Actual prior native modules differ from the independently bound complete native inventory')
        graph = compare_graphs(gzip.decompress(shared.checked(root, row['graph']).read_bytes()),
                               gzip.decompress(shared.checked(root, row['prior_graph']).read_bytes()))
        facts[target] = {'elf_sha256': row['elf_sha256'], 'prior_elf_sha256': previous[target], **native, **graph}
    require(facts == profile['migration_facts'], 'Recorded migration facts differ from complete recomputation')
    return facts


def verify_runtime_migration(profile, artifact, root=shared.ROOT):
    paths = profile['runtime_evidence']
    require(set(paths.values()) <= profile['evidence_sha256'].keys(), 'Complete runtime manifests are not bound')
    previous = json.loads(gzip.decompress(shared.checked(root, paths['prior']).read_bytes()))
    current = json.loads(gzip.decompress(shared.checked(root, paths['current']).read_bytes()))
    require(shared.manifest_hash(previous) == profile['prior_runtime_manifest_sha256']
            and shared.manifest_hash(current) == profile['expected']['runtime_manifest_sha256'],
            'Complete runtime manifest identity differs')
    if artifact == 'grafana':
        require(paths['prior'] == 'deployment/security/grafana-source-20261110-evidence/runtime/runtime-manifest-canonical.json.gz'
                and profile['prior_runtime_manifest_sha256'] == shared.load_profile(snapshot(root))['expected']['runtime_manifest_sha256'],
                'Prior full Grafana runtime differs')
        prefix = 'usr/share/deepresearch/grafana-candidate/'
    else:
        require(paths['prior'] == 'deployment/security/minio-source-20261112-evidence/prior-runtime-manifest.json.gz',
                'Prior full MinIO runtime differs')
        prefix = 'usr/share/deepresearch-build/'
    before, after = {row['path']: row for row in previous}, {row['path']: row for row in current}
    require(len(before) == len(previous) and len(after) == len(current), 'Duplicate runtime manifest path')
    changes = []
    native = set(profile['expected']['native_sha256'])
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        if new is None:
            require(artifact == 'minio' and name in {
                'usr/share/deepresearch-build/csv-regression.txt',
                'usr/share/deepresearch-build/legacy-multipart-regression.txt'},
                'Retained runtime file was removed')
            changes.append({'path': name, 'before': old, 'after': None})
            continue
        if name in native:
            require(old is not None and {k: v for k, v in new.items() if k not in ('sha256', 'bytes')}
                    == {k: v for k, v in old.items() if k not in ('sha256', 'bytes')},
                    'Actual native permissions or ownership changed')
            require(new['sha256'] == profile['expected']['native_sha256'][name], 'Actual native manifest identity differs')
        else:
            require(name.startswith(prefix) and new['type'] in {'file', 'directory'},
                    'Runtime changes extend beyond rebuilt Go executables and their documentary build evidence')
            if new['type'] == 'file':
                require(not new['mode'] & 0o111 and (name.endswith(('.json', '.json.gz', '.txt'))
                        or name.rsplit('/', 1)[-1] in {'go.mod', 'go.sum', 'go.work', 'go.work.sum', 'Go-LICENSE'}),
                        'New build evidence must be a non-executable documentary file')
        changes.append({'path': name, 'before': old, 'after': new})
    require(native <= {row['path'] for row in changes}, 'Not all affected Go executables actually changed')
    require(changes == profile['runtime_changes'], 'Actual full runtime differences differ from reviewed paths')
    return changes


def scan_rows(report):
    rows = []
    for result in report['Results']:
        target = ('os:' + result['Type']) if result.get('Class') == 'os-pkgs' else result['Target'].lstrip('/')
        rows.extend((item['VulnerabilityID'], target, item['PkgName'], item['InstalledVersion'], item['Severity'])
                    for item in result.get('Vulnerabilities', []))
    return rows


def verify_scan_migration(profile, root=shared.ROOT):
    from collections import Counter
    paths = profile['scan_migration']
    require({paths['prior'], paths['current']} <= profile['evidence_sha256'].keys(),
            'Both complete original raw scans must be bound')
    reports = {name: json.loads(gzip.decompress(shared.checked(root, paths[name]).read_bytes()))
               for name in ('prior', 'current')}
    old, current = (Counter(scan_rows(reports[name])) for name in ('prior', 'current'))
    removed = Counter((CVE, target, 'stdlib', 'v1.27.3', 'UNKNOWN')
                      for target in profile['expected']['native_sha256'])
    require(old == current + removed, 'Raw scan differences exceed the exact fixed stdlib target rows')
    require(not any(row[0] == CVE for row in current), 'The fixed advisory remains in the actual candidate scan')
    return {'prior_count': sum(old.values()), 'current_count': sum(current.values()),
            'fixed_stdlib_rows': sum(removed.values()), 'unrelated_rows_unchanged': True,
            'raw_reports_modified': False}


def verify_trivy_source(report, inspection, receipt):
    metadata = report.get('Metadata', {})
    # Docker's OCI image store and a saved-archive scanner expose different
    # identity nodes. The portable config is independently hashed at export;
    # neither an arbitrary alias nor a tag can replace that content chain.
    require(metadata.get('ImageID') in {inspection['Id'], receipt['config_digest']}
            and metadata.get('DiffIDs') == receipt['diff_ids'], 'Complete scan belongs to another image or layer chain')
    config = metadata.get('ImageConfig', {})
    require(config.get('rootfs', {}).get('diff_ids') == receipt['diff_ids']
            and config.get('os') == inspection['Os'] and config.get('architecture') == inspection['Architecture']
            and shared.runtime_config(config.get('config', {})) == receipt['runtime_config']
            and config.get('config', {}).get('Labels') == inspection['Config'].get('Labels'),
            'Scanned portable config differs from actual platform, runtime settings or build provenance')
    return 'engine_image_id' if metadata['ImageID'] == inspection['Id'] else 'portable_config_digest'
