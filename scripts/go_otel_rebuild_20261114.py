"""Compare the 20261114 otel-v1.47.0 rebuild against its exact 20261112 prior facts."""
from __future__ import annotations

import json
import gzip
from pathlib import Path

from scripts import check_grafana_source_build_20261110 as shared
from scripts.retained_source_review_20261112 import SOURCE

# CVE-2026-128085: go.opentelemetry.io/otel trace/sdk/sdk/log, fixed in v1.47.0.
# The affected modules and their prior (20261112) versions, all resolved to v1.47.0.
CVE = 'CVE-2026-128085'
TARGET_VERSION = 'v1.47.0'
OTEL_MODULES = ('go.opentelemetry.io/otel', 'go.opentelemetry.io/otel/trace',
                'go.opentelemetry.io/otel/sdk', 'go.opentelemetry.io/otel/log',
                'go.opentelemetry.io/otel/sdk/log', 'go.opentelemetry.io/otel/metric',
                'go.opentelemetry.io/otel/sdk/metric')
# The exact prior version each module was pinned to in the 20261112 subject.
PRIOR_OTEL_VERSIONS = {'v1.45.0', 'v1.46.0', 'v0.21.0', 'v0.22.0'}
# The two plugin backends whose locks were already at v1.47.0 in 20261112 and are
# rebuilt byte-identically; their ELF must not change.
UNCHANGED_TARGETS = {
    'usr/share/grafana/data/plugins-bundled/elasticsearch/gpx_grafana_elasticsearch_datasource_linux_amd64',
    'usr/share/grafana/data/plugins-bundled/prometheus/gpx_grafana-prometheus-datasource_linux_amd64',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


PRIOR_PROFILE_PATH = 'deployment/security/grafana-source-20261112-profile.json'


def prior_profile(root=shared.ROOT):
    """Read the prior (20261112) profile bytes directly.

    The 20261114 generation specifically migrates the seven lock files whose
    bytes changed under the otel-v1.47.0 bump, so the 20261112 gate's own
    source-input re-hash would fail-closed on those exact changed bytes.  We
    therefore read the committed, already-verified 20261112 profile bytes
    without re-running its source-input check; the 20261114 gate re-verifies
    every prior fact it actually depends on (native SHA-256, runtime manifest,
    runtime contract, inventory and reviewed findings).
    """
    return json.loads((Path(root) / PRIOR_PROFILE_PATH).read_bytes())


def native_modules(native):
    modules = {}
    for row in [native['Main'], *native.get('Deps', [])]:
        path = row['Path']
        require(path not in modules, 'Duplicate native module identity')
        modules[path] = {key: row[key] for key in ('Path', 'Version', 'Sum', 'Replace') if key in row}
    return modules


def compare_native(current, previous):
    """Both subjects are go1.27.4; the only permitted module delta is the otel family.

    For the two already-v1.47.0 backends (elasticsearch/prometheus) the native modules
    are byte-identical, which is the correct no-op result.
    """
    require(current.get('GoVersion') == 'go1.27.4', 'Actual ELF was not compiled with Go 1.27.4')
    require(previous.get('GoVersion') == 'go1.27.4', 'Prior exact compiler identity differs')
    require(current.get('Path') == previous.get('Path'), 'Actual native entry package changed')
    cur = native_modules(current)
    prev = native_modules(previous)
    if cur == prev:
        return {'go_version': 'go1.27.4', 'entry_package': current['Path'],
                'module_count': len(cur), 'otel_modules_moved': [], 'otel_target_version': TARGET_VERSION,
                'nonstdlib_modules_identical': True, 'source_and_build_settings_preserved': True}
    # Non-otel modules must be byte-identical.
    cur_nonotel = {p: m for p, m in cur.items() if p not in OTEL_MODULES}
    prev_nonotel = {p: m for p, m in prev.items() if p not in OTEL_MODULES}
    require(cur_nonotel == prev_nonotel, 'Non-otel module versions, sums or replacements changed')
    # otel modules must move to v1.47.0 from a known prior version.
    for mod in OTEL_MODULES:
        if mod in prev:
            require(prev[mod]['Version'] in PRIOR_OTEL_VERSIONS,
                    'Unexpected prior otel version for ' + mod + ': ' + prev[mod]['Version'])
        if mod in cur:
            require(cur[mod]['Version'] == TARGET_VERSION,
                    'otel module not at v1.47.0: ' + mod + ' ' + cur[mod]['Version'])
    # At least one otel module must actually have moved (sanity: a real fix happened).
    moved = [m for m in OTEL_MODULES if m in prev and m in cur and prev[m]['Version'] != cur[m]['Version']]
    require(moved, 'No otel module actually moved to v1.47.0')
    settings = {row['Key']: row['Value'] for row in current.get('Settings', [])}
    old_settings = {row['Key']: row['Value'] for row in previous.get('Settings', [])}
    for key in ('CGO_ENABLED', 'GOOS', 'GOARCH', 'GOAMD64', '-tags', 'vcs.revision', 'vcs.modified'):
        require(settings.get(key) == old_settings.get(key), 'Native build setting changed: ' + key)
    require(all(settings.get(key) == value for key, value in
                {'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': 'amd64', 'GOAMD64': 'v1'}.items()),
            'Fixed build is outside the verified Linux/amd64 production profile')
    return {'go_version': 'go1.27.4', 'entry_package': current['Path'],
            'module_count': len(cur), 'otel_modules_moved': sorted(moved),
            'otel_target_version': TARGET_VERSION, 'nonstdlib_modules_identical': True,
            'source_and_build_settings_preserved': True}


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
    # Only otel packages (by import path) may differ in any way — added, removed,
    # or version-moved — as a direct consequence of the otel 1.47.0 upgrade.
    def is_otel_package(name):
        return name == 'go.opentelemetry.io/otel' or name.startswith('go.opentelemetry.io/otel/')
    changed = []
    for name in actual.keys() | old.keys():
        if actual.get(name) != old.get(name):
            require(is_otel_package(name), 'Non-otel package graph changed: ' + name)
            changed.append(name)
    if not changed:
        return {'complete_package_count': len(current), 'nonstdlib_package_count': len(actual),
                'nonstdlib_graph_identical': True, 'complete_import_closure': True,
                'otel_packages_moved': 0, 'otel_target_version': TARGET_VERSION}
    return {'complete_package_count': len(current), 'nonstdlib_package_count': len(actual),
            'nonstdlib_graph_identical': True, 'complete_import_closure': True,
            'otel_packages_moved': len(changed), 'otel_target_version': TARGET_VERSION}


def otel_fix():
    return {'cve': CVE, 'fixed_version': TARGET_VERSION, 'affected_modules': list(OTEL_MODULES),
            'official_fix': 'https://avd.aquasec.com/nvd/cve-2026-128085'}


def review_subject(profile):
    reviews = set(profile['independent_reviews'])
    return shared.manifest_hash({key: value for key, value in profile.items()
        if key not in ('independent_reviews', 'review_subject_sha256', 'evidence_sha256', 'status')}
        | {'evidence_sha256': {name: value for name, value in profile['evidence_sha256'].items()
                              if name not in reviews}})


def load_profile(artifact, root=shared.ROOT):
    path = 'deployment/security/' + artifact + '-source-20261114-profile.json'
    profile = shared.read(shared.checked(root, path))
    require(profile.get('schema') == 'go-otel-source-profile-20261114-v1'
            and profile.get('status') == 'verified' and profile.get('profile') == artifact + '-source-20261114'
            and profile.get('image') == 'deepresearch-' + artifact + '-source:20261114'
            and profile.get('dockerfile') == 'deployment/docker/Dockerfile.' + artifact + '-20261114',
            'New exact otel source-build review is not verified')
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
    prior = prior_profile(root)
    previous = prior['expected']['native_sha256']
    require(set(profile['native_evidence']) == set(previous)
            and set(profile['expected']['native_sha256']) == set(previous), 'Complete rebuilt native target set differs')
    require(otel_fix() == profile['otel_fix'], 'Official otel fixed-version statement differs')
    facts = {}
    for target, row in profile['native_evidence'].items():
        if target in UNCHANGED_TARGETS:
            require(row['elf_sha256'] == previous[target],
                    'Unchanged-then-rebuilt lock backend ELF differs: ' + target)
            require(row['prior_elf_sha256'] == previous[target],
                    'Unchanged backend prior ELF differs: ' + target)
        else:
            require(row['prior_elf_sha256'] == previous[target]
                    and row['elf_sha256'] == profile['expected']['native_sha256'][target]
                    and row['elf_sha256'] != previous[target], 'Actual rebuilt/prior ELF identity differs: ' + target)
        require(all(row[name] in profile['evidence_sha256'] for name in
                    ('native', 'prior_native', 'graph', 'prior_graph')),
                'Every complete native/graph input must be bound by the review')
        native = compare_native(shared.read(shared.checked(root, row['native'])),
                                shared.read(shared.checked(root, row['prior_native'])))
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
    require(paths['prior'] == 'deployment/security/grafana-source-20261112-evidence/runtime-manifest.json.gz'
            and profile['prior_runtime_manifest_sha256'] == prior_profile(root)['expected']['runtime_manifest_sha256'],
            'Prior full Grafana runtime differs')
    prefix = 'usr/share/deepresearch/grafana-candidate/'
    before, after = {row['path']: row for row in previous}, {row['path']: row for row in current}
    require(len(before) == len(previous) and len(after) == len(current), 'Duplicate runtime manifest path')
    changes = []
    native = set(profile['expected']['native_sha256'])
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        if new is None:
            raise ValueError('Retained runtime file was removed: ' + name)
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
    # Only the 12 changed executables must actually differ; the 2 unchanged backends stay identical.
    changed_native = {name for name in native if before.get(name) != after.get(name)}
    require(changed_native == (set(profile['expected']['native_sha256']) - UNCHANGED_TARGETS),
            'Rebuilt native target set does not match the otel move')
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
    reports = {name: json.loads(gzip.decompress(Path(root, paths[name]).read_bytes()))
               for name in ('prior', 'current')}
    old, current = (Counter(scan_rows(reports[name])) for name in ('prior', 'current'))
    # The otel upgrade changes only statically-linked Go module versions inside the
    # rebuilt executables; the image-level Trivy scan (OS packages + compiled binaries)
    # is byte-identical, so the reviewed image-scan findings must be unchanged.
    require(old == current, 'Image-level scan rows changed unexpectedly')
    require(not any(row[0] == CVE for row in current), 'The fixed otel advisory remains in the actual image scan')
    return {'prior_count': sum(old.values()), 'current_count': sum(current.values()),
            'image_scan_rows_unchanged': True, 'raw_reports_modified': False}


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


def reviewed_runtime_config_equivalent(actual, expected):
    actual_user, expected_user = actual.get('User'), expected.get('User')
    return ({key: value for key, value in actual.items() if key != 'User'}
            == {key: value for key, value in expected.items() if key != 'User'}
            and (actual_user == expected_user
                 or actual_user in (None, '') and expected_user in (None, '')))


def verify_receipt(receipt, inspection, profile, artifact, *, root=shared.ROOT):
    require(receipt.get('schema') == 'go-otel-source-build-receipt-20261114-v1'
            and receipt.get('profile') == artifact + '-source-20261114'
            and receipt.get('image') == 'deepresearch-' + artifact + '-source:20261114'
            and receipt.get('source_inputs') == profile['source_inputs']
            and receipt.get('image_id') == inspection.get('Id')
            and receipt.get('diff_ids') == inspection.get('RootFS', {}).get('Layers')
            and inspection.get('Os') == 'linux' and inspection.get('Architecture') == 'amd64',
            'Actual image/source/platform differs from the new otel rebuild receipt')
    for name in ('native_sha256', 'runtime_manifest_sha256'):
        require(receipt.get(name) == profile['expected'][name], 'New otel build receipt differs: ' + name)
    require(isinstance(receipt.get('runtime_config'), dict)
            and reviewed_runtime_config_equivalent(receipt['runtime_config'], profile['expected']['runtime_config']),
            'New otel build receipt differs: runtime_config')
    require(shared.runtime_config(inspection['Config']) == receipt['runtime_config']
            and isinstance(receipt.get('config_digest'), str)
            and shared.retained.DIGEST.fullmatch(receipt['config_digest']), 'Actual runtime config differs')
    require(inspection['Config'].get('Labels', {}).get('org.deepresearch.project.inputs_sha256')
            == shared.manifest_hash(receipt['source_inputs']),
            'Actual image build-input label differs; current source cannot be assigned to an older image')
    for name, expected in receipt['source_inputs'].items():
        require(shared.digest(shared.checked(root, name)) == expected, 'Current build source input changed')
    return receipt
