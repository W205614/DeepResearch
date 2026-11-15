"""Deterministically verify the exact Grafana native/SBOM inventory migration.

No scanner execution or clock changes occur here. Current scans have separate
freshness checks; archived source-review facts retain their original timestamps.
"""
from collections import Counter
import base64
import gzip
import hashlib
import json
from pathlib import Path

from scripts import check_grafana_source_build_20261110 as old
from scripts import go_otel_rebuild_20261114 as common
from scripts.check_image_secrets import assess as assess_secrets
from scripts.check_native_jvm import validate_source

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = 'deployment/security/grafana-source-20261114-evidence/'

INVENTORY_REPORTS = {
    'raw-trivy.private.json': 'raw-trivy.json.gz',
    'full-syft.json': 'full-syft.json.gz',
    'full-grype.json': 'full-grype.json.gz',
    'build-receipt.json': 'build-receipt.json.gz',
    'image-inspect.json': 'image-inspect.json.gz',
    'collector.json': 'collector.json.gz',
}
INVENTORY_EVIDENCE = EVIDENCE + 'normal-scans-20261115-02/'


def inventory_bytes(directory, name):
    original, archived = directory / name, directory / INVENTORY_REPORTS[name]
    common.require(original.is_file() != archived.is_file(),
                   'Exactly one original or archived complete scanner input is required')
    return original.read_bytes() if original.is_file() else gzip.decompress(archived.read_bytes())


def native_module_inventory(sbom, native_evidence, field, *, root=ROOT):
    expected, natives = Counter(), {}
    for target, row in native_evidence.items():
        native = old.read(root / row[field])
        natives[target] = native
        expected[(target, 'stdlib', native['GoVersion'], '')] += 1
        for module in [native['Main'], *native['Deps']]:
            replacement = module.get('Replace')
            if replacement and replacement['Path'].startswith('.'):
                name, version, checksum = module['Path'], 'UNKNOWN', ''
            else:
                effective = replacement or module
                name, version = effective['Path'], effective.get('Version') or 'UNKNOWN'
                checksum = effective.get('Sum', '')
                if version == '(devel)':
                    common.require(not checksum, 'A local development module unexpectedly has a remote checksum')
                    version = 'UNKNOWN'
            expected[(target, name, version, checksum)] += 1
    actual = Counter()
    for artifact in sbom['artifacts']:
        if artifact['type'] != 'go-module':
            continue
        common.require(artifact['foundBy'] == 'go-module-binary-cataloger'
                       and artifact['metadataType'] == 'go-module-buildinfo-entry'
                       and len(artifact['locations']) == 1,
                       'Complete native Go SBOM identity or location differs')
        target = artifact['locations'][0]['path'].removeprefix('/')
        common.require(target in natives, 'A Go SBOM artifact belongs to an unreviewed executable')
        metadata, native = artifact['metadata'], natives[target]
        standard = artifact['name'] == 'stdlib'
        common.require(metadata['goCompiledVersion'] == native['GoVersion']
                       and metadata['architecture'] == ('' if standard else 'amd64')
                       and (metadata.get('mainModule') is None if standard
                            else metadata['mainModule'] == native['Main']['Path']),
                       'Go SBOM compiler, architecture or main-module identity differs')
        actual[(target, artifact['name'], artifact['version'], metadata.get('h1Digest', ''))] += 1
    common.require(actual == expected, 'The complete Go SBOM differs from every native module identity/checksum')
    return expected


def other_inventory(sbom):
    return Counter((a['type'], a['name'], a['version'],
                    tuple(sorted({location['path'] for location in a['locations']})))
                   for a in sbom['artifacts'] if a['type'] != 'go-module')


def verify_inventory(profile, prior, native_evidence, directory, *, root=ROOT):
    trivy_bytes = inventory_bytes(directory, 'raw-trivy.private.json')
    trivy = json.loads(trivy_bytes)
    sbom = json.loads(inventory_bytes(directory, 'full-syft.json'))
    grype = json.loads(inventory_bytes(directory, 'full-grype.json'))
    receipt = json.loads(inventory_bytes(directory, 'build-receipt.json'))
    inspection = json.loads(inventory_bytes(directory, 'image-inspect.json'))
    collector = json.loads(inventory_bytes(directory, 'collector.json'))
    common.verify_receipt(receipt, inspection, profile, 'grafana', root=root)
    common.require(collector['operational_collection'] is True
                   and type(collector['preparation_only']) is bool
                   and receipt['preparation_only'] is collector['preparation_only']
                   and collector['sourceBuild']['profile_review_verified'] is not collector['preparation_only']
                   and collector['native_audit']['native_sha256'] == profile['expected']['native_sha256']
                   and collector['sourceBuild']['actualImageID'] == receipt['image_id']
                   and collector['sourceBuild']['portableConfigDigest'] == receipt['config_digest']
                   and collector['sourceBuild']['buildReceiptSha256'] == hashlib.sha256(inventory_bytes(directory, 'build-receipt.json')).hexdigest()
                   and collector['sbom_sha256'] == hashlib.sha256(inventory_bytes(directory, 'full-syft.json')).hexdigest()
                   and collector['raw_grype_sha256'] == hashlib.sha256(inventory_bytes(directory, 'full-grype.json')).hexdigest(),
                   'Fresh complete native collection is not bound to the actual normal build')
    validate_source(sbom, inspection)
    config_raw = base64.b64decode(sbom['source']['metadata']['config'], validate=True)
    config = json.loads(config_raw)
    common.require('sha256:' + hashlib.sha256(config_raw).hexdigest() == receipt['config_digest']
                   and sbom['source']['metadata']['imageID'] == receipt['config_digest']
                   and config['rootfs']['diff_ids'] == receipt['diff_ids']
                   and old.runtime_config(config.get('config', {})) == receipt['runtime_config'],
                   'The complete SBOM portable configuration differs')
    previous = json.loads(gzip.decompress((root / 'deployment/security/grafana-source-20261112-evidence/full-syft.json.gz').read_bytes()))
    before = native_module_inventory(previous, native_evidence, 'prior_native', root=root)
    after = native_module_inventory(sbom, native_evidence, 'native', root=root)
    common.require(other_inventory(previous) == other_inventory(sbom),
                   'Non-Go OS, JavaScript or binary SBOM inventory changed')
    inventory = {}
    go_targets = set()
    for result in trivy['Results']:
        key = result['Type'] if result.get('Class') == 'os-pkgs' else result['Target']
        common.require(key not in inventory, 'Complete Trivy package inventory has duplicate targets')
        packages = old.retained.shared.package_counter(result['Packages'])
        inventory[key] = old.manifest_hash(sorted(packages.items()))
        if key in native_evidence:
            common.require(result['Type'] == 'gobinary', 'A reviewed native target lost its complete Go inventory')
            expected_packages = Counter()
            native = old.read(root / native_evidence[key]['native'])
            local_replacements = {module['Path']: module['Replace']['Path'] for module in native['Deps']
                                  if module.get('Replace', {}).get('Path', '').startswith('.')}
            for (target, name, version, _checksum), count in after.items():
                if target == key:
                    scanner_version = ('v' + version.removeprefix('go') if name == 'stdlib'
                                       else None if version == 'UNKNOWN' else version)
                    expected_packages[(local_replacements.get(name, name), scanner_version)] += count
            common.require(packages == expected_packages,
                           'The full Trivy Go inventory differs from the actual native modules')
            go_targets.add(key)
        else:
            common.require(inventory[key] == prior['expected']['trivy_inventory_sha256'].get(key),
                           'Non-Go OS or JavaScript Trivy inventory changed')
    common.require(go_targets == native_evidence.keys()
                   and inventory.keys() == prior['expected']['trivy_inventory_sha256'].keys(),
                   'Complete Trivy OS, JavaScript or native target coverage differs')
    expected = {'trivy_inventory_sha256': inventory,
                'syft_types': dict(Counter(a['type'] for a in sbom['artifacts']))}
    old.retained.shared.empty_filter_markers(trivy)
    common.require(assess_secrets(trivy)['passed'], 'Secret findings block inventory evidence')
    common.require(trivy.get('SchemaVersion') == 2 and trivy.get('ArtifactType') == 'container_image'
                   and trivy.get('Trivy') == {'Version': '0.75.0'} and not trivy.get('Errors'),
                   'Complete pinned Trivy report is required')
    common.verify_trivy_source(trivy, inspection, receipt)
    descriptor = grype.get('descriptor', {})
    common.require(descriptor.get('name') == 'grype' and descriptor.get('version') == '0.121.0'
                   and descriptor.get('db', {}).get('status', {}).get('valid') is True
                   and not grype.get('errors') and not grype.get('Errors')
                   and grype.get('source', {}).get('target', {}).get('imageID') == receipt['config_digest'],
                   'Complete pinned Grype report and actual image identity are required')
    observed = old.full_scan_rows(trivy, grype)
    for scanner, rows in observed.items():
        common.require(Counter(map(old.finding_key, rows))
                       == Counter(map(old.finding_key, prior['reviewed_findings'][scanner])),
                       'Original exact all-severity scanner findings changed')
    before_paths = {(target, name) for target, name, _version, _checksum in before}
    added = [{'target': target, 'module': name, 'version': version, 'sum': checksum}
             for target, name, version, checksum in sorted(after)
             if (target, name) not in before_paths]
    common.require(len(added) == 10 and all(row['module'] == 'go.opentelemetry.io/otel/log'
                                          and row['version'] == 'v1.47.0' for row in added),
                   'Unexpected newly linked modules outside the reviewed otel/log migration')
    facts = {'prior_go_modules': sum(before.values()), 'current_go_modules': sum(after.values()),
             'newly_linked_modules': added,
             'non_go_inventory_unchanged': True,
             'native_module_identity_and_h1_coverage_verified': True,
             'trivy_changed_inventory_targets': sorted(name for name in inventory
                 if inventory[name] != prior['expected']['trivy_inventory_sha256'].get(name)),
             'report_sha256': {name: hashlib.sha256(inventory_bytes(directory, name)).hexdigest()
                               for name in INVENTORY_REPORTS},
             'collection_preparation_only': collector['preparation_only'],
             'all_severity_findings': {name: len(rows) for name, rows in observed.items()},
             'raw_reports_modified': False}
    return expected, facts


