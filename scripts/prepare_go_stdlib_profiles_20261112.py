"""Derive review drafts from actual built images and complete raw scans; never self-approve them."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import shutil
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_build_20261110 as old  # noqa: E402
from scripts import build_go_stdlib_security_20261112 as builder  # noqa: E402
from scripts import go_stdlib_rebuild_20261112 as common  # noqa: E402
from scripts.capture_go_native_20261112 import capture as native_capture  # noqa: E402
from scripts.image_disposition import load_binding as minio_binding  # noqa: E402
from scripts.retained_source_review_20261112 import SOURCE, snapshot  # noqa: E402


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def relative(path):
    return path.relative_to(ROOT).as_posix()


def current_inputs(receipt, artifact):
    common.require(receipt['source_inputs'] == builder.source_inputs(artifact),
                   'Prepared image inputs differ from the current exact build inputs')


def create(args):
    artifact, build = args.artifact, args.build_dir.resolve()
    receipt = old.read(build / 'build-receipt.json')
    current_inputs(receipt, artifact)
    common.require(receipt['profile'] == artifact + '-source-20261112', 'Actual build belongs to another artifact')
    evidence = ROOT / ('deployment/security/' + artifact + '-source-20261112-evidence')
    evidence.mkdir(parents=True, exist_ok=True)
    previous_profile = old.load_profile(snapshot(ROOT)) if artifact == 'grafana' else None
    previous = previous_profile['expected']['native_sha256'] if previous_profile else minio_binding()['binary_sha256']
    actual = native_capture(receipt['image_id'], receipt['native_sha256'],
                            build / ('independent-native-' + uuid.uuid4().hex[:12]))
    native_evidence = {}
    with tarfile.open(build / 'actual-payload/rootfs.tar') as archive:
        modules = old.read(ROOT / 'deployment/vendors/grafana-20261107/plugin-modules.json') if artifact == 'grafana' else []
        names = {old.MAIN: 'main'} | {'usr/share/grafana/data/plugins-bundled/' + row['plugin_directory'] + '/' + row['binary']:
                                    row['plugin_directory'] for row in modules}
        for target, old_elf in previous.items():
            name = names[target] if artifact == 'grafana' else target.rsplit('/', 1)[-1]
            native_path = evidence / (name + '-native.json' if artifact == 'grafana' else name + '-new-native.json')
            shutil.copyfile(actual[target]['native_file'], native_path)
            if artifact == 'grafana':
                if name == 'main':
                    prior_native = previous_profile['expected']['source_scan']['main']['native']
                    prior_graph = previous_profile['expected']['source_scan']['main']['graph']
                    member = 'usr/share/deepresearch/grafana-candidate/graphs/main/main.json.gz'
                else:
                    prior_native = 'deployment/security/grafana-fixed-20261107-evidence/native/' + name + '-native-buildinfo.json'
                    prior_graph = 'deployment/security/grafana-fixed-20261107-evidence/native/build-evidence/graphs/plugins/' + name + '.json.gz'
                    member = 'usr/share/deepresearch/grafana-candidate/graphs/plugins/' + name + '.json.gz'
                graph = archive.extractfile(member).read()
            else:
                prior_native = 'deployment/security/minio-source-20261112-evidence/' + name + '-prior-native.json'
                prior_graph = 'deployment/security/minio-html4-evidence/' + name + '-production-graph-complete.json.gz'
                graph = gzip.compress(archive.extractfile('usr/share/deepresearch-build/' + name + '-graph.json').read(), mtime=0)
            graph_path = evidence / (name + '-graph.json.gz')
            graph_path.write_bytes(graph)
            native_evidence[target] = {'elf_sha256': receipt['native_sha256'][target], 'prior_elf_sha256': old_elf,
                'native': relative(native_path), 'prior_native': prior_native, 'graph': relative(graph_path),
                'prior_graph': prior_graph}
    shutil.copyfile(build / 'actual-payload/runtime-manifest.json.gz', evidence / 'runtime-manifest.json.gz')
    return finish(args, receipt, evidence, previous_profile, native_evidence)


def finish(args, receipt, evidence, previous_profile, native_evidence):
    artifact = args.artifact
    trivy = old.read(args.trivy)
    from scripts.check_image_secrets import assess
    common.require(assess(trivy)['passed'], 'Secret findings block publishing any raw scan evidence')
    common.verify_trivy_source(trivy, old.read(args.build_dir / 'image-inspect.json'), receipt)
    inventory = {}
    rows = []
    os_targets = set()
    for result in trivy['Results']:
        key = result['Type'] if result.get('Class') == 'os-pkgs' else result['Target']
        common.require(key not in inventory, 'Complete inventory has duplicate targets')
        inventory[key] = old.manifest_hash(sorted(old.retained.shared.package_counter(result['Packages']).items()))
        if result.get('Class') == 'os-pkgs':
            os_targets.add('os:' + result['Type'])
        target = 'os:' + result['Type'] if result.get('Class') == 'os-pkgs' else result['Target']
        rows.extend({'id': finding['VulnerabilityID'], 'target': target,
                     'package': finding['PkgName'], 'version': finding['InstalledVersion'],
                     'severity': finding['Severity']} for finding in result.get('Vulnerabilities', []))
    raw_path = evidence / 'raw-trivy.json.gz'
    raw_path.write_bytes(gzip.compress(args.trivy.read_bytes(), mtime=0))
    common.require(assess(old.read(args.prior_trivy))['passed'], 'Prior secret findings block raw evidence publication')
    (evidence / 'prior-raw-trivy.json.gz').write_bytes(gzip.compress(args.prior_trivy.read_bytes(), mtime=0))
    expected = {key: receipt[key] for key in ('native_sha256', 'runtime_manifest_sha256', 'runtime_config')}
    expected['trivy_inventory_sha256'] = inventory
    reviewed, unreviewed = {'Trivy': rows}, []
    if artifact == 'grafana':
        common.require(args.grype is not None and args.sbom is not None, 'Complete native reports are required')
        native = old.read(args.grype)
        sbom = old.read(args.sbom)
        observed = old.full_scan_rows(trivy, native)
        for scanner in ('Trivy', 'Grype'):
            common.require(Counter(old.finding_key(row) for row in observed[scanner])
                           == Counter(old.finding_key(row) for row in previous_profile['reviewed_findings'][scanner]),
                           'New, missing, duplicate or changed remaining ' + scanner + ' findings block the draft')
        reviewed = observed
        expected['syft_types'] = dict(Counter(row['type'] for row in sbom['artifacts']))
        (evidence / 'raw-grype.json.gz').write_bytes(gzip.compress(args.grype.read_bytes(), mtime=0))
        (evidence / 'full-syft.json.gz').write_bytes(gzip.compress(args.sbom.read_bytes(), mtime=0))
        prior_manifest = 'deployment/security/grafana-source-20261110-evidence/runtime/runtime-manifest-canonical.json.gz'
        prior_hash = previous_profile['expected']['runtime_manifest_sha256']
    else:
        known = {'CVE-2026-39414', 'GO-2026-5932', 'CVE-2026-78661', 'CVE-2026-97045'}
        reviewed = {'Trivy': [row for row in rows if row['id'] in known]}
        unreviewed = [row for row in rows if row['id'] not in known]
        common.require(all(row['severity'] in {'LOW', 'MEDIUM'} and row['target'] in os_targets for row in unreviewed)
                       and len(unreviewed) == 34, 'New threshold or changed existing OS rows block the draft')
        prior_manifest = 'deployment/security/minio-source-20261112-evidence/prior-runtime-manifest.json.gz'
        prior_hash = old.manifest_hash(json.loads(gzip.decompress((ROOT / prior_manifest).read_bytes())))
        for name in ('csv-regression.txt', 'legacy-multipart-regression.txt'):
            shutil.copyfile(args.build_dir / 'regressions' / name, evidence / name)
    profile = {'schema': 'go-stdlib-source-profile-20261112-v1', 'status': 'prepared',
        'profile': artifact + '-source-20261112', 'image': 'deepresearch-' + artifact + '-source:20261112',
        'dockerfile': 'deployment/docker/Dockerfile.' + artifact + '-20261112', 'context': '.',
        'source_inputs': receipt['source_inputs'], 'expected': expected, 'reviewed_findings': reviewed,
        'historical_review_source': SOURCE, 'old_binary_approval_inherited': False,
        'binary_redistribution_approved': False, 'native_evidence': native_evidence,
        'stdlib_fix': common.check_advisory(old.read(ROOT / 'deployment/security/go-stdlib-20261112-evidence/GO-2026-6859.json')),
        'prior_runtime_manifest_sha256': prior_hash,
        'runtime_evidence': {'prior': prior_manifest, 'current': relative(evidence / 'runtime-manifest.json.gz')},
        'scan_migration': {'prior': relative(evidence / 'prior-raw-trivy.json.gz'), 'current': relative(raw_path)},
        'independent_reviews': [relative(evidence / 'review-root.json'), relative(evidence / 'review-release-prepare.json')]}
    if artifact == 'minio':
        profile.update(threshold_severities=['HIGH', 'CRITICAL', 'UNKNOWN'],
            retained_unreviewed_os_findings=unreviewed,
            regressions={'csv': relative(evidence / 'csv-regression.txt'),
                         'legacy': relative(evidence / 'legacy-multipart-regression.txt')})
    return complete(args, profile, evidence)


def complete(args, profile, evidence):
    files = {relative(path) for path in evidence.rglob('*') if path.is_file()
             and path.name not in {'review-root.json', 'review-release-prepare.json'}}
    files.add(profile['runtime_evidence']['prior'])
    for row in profile['native_evidence'].values():
        files.update(row[name] for name in ('native', 'prior_native', 'graph', 'prior_graph'))
    files.update(relative(path) for path in (ROOT / 'deployment/security/go-stdlib-20261112-evidence').iterdir() if path.is_file())
    files.update({'scripts/go_stdlib_rebuild_20261112.py', 'scripts/capture_go_native_20261112.py',
                  'scripts/check_' + args.artifact + '_source_build_20261112.py',
                  'scripts/prepare_go_stdlib_profiles_20261112.py', 'tests/test_go_stdlib_rebuild_20261112.py'})
    if args.artifact == 'grafana':
        files.update({'deployment/security/grafana-source-20261110-profile.json',
                      'scripts/scan_grafana_source_20261112.py', 'scripts/check_grafana_source_scan_20261112.py',
                      'scripts/retained_source_review_20261112.py', 'scripts/go_native_database_transport_20261112.py'})
    profile['evidence_sha256'] = {name: old.digest(old.checked(ROOT, name)) for name in sorted(files)}
    facts = {}
    for target, row in profile['native_evidence'].items():
        facts[target] = {'elf_sha256': row['elf_sha256'], 'prior_elf_sha256': row['prior_elf_sha256'],
            **common.compare_native(old.read(ROOT / row['native']), old.read(ROOT / row['prior_native'])),
            **common.compare_graphs(gzip.decompress((ROOT / row['graph']).read_bytes()),
                                    gzip.decompress((ROOT / row['prior_graph']).read_bytes()))}
    profile['migration_facts'] = facts
    before = {row['path']: row for row in json.loads(gzip.decompress((ROOT / profile['runtime_evidence']['prior']).read_bytes()))}
    after = {row['path']: row for row in json.loads(gzip.decompress((ROOT / profile['runtime_evidence']['current']).read_bytes()))}
    profile['runtime_changes'] = [{'path': name, 'before': before.get(name), 'after': after.get(name)}
        for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
    common.verify_migrations(profile, args.artifact)
    common.verify_runtime_migration(profile, args.artifact)
    profile['scan_migration_facts'] = common.verify_scan_migration(profile)
    builder.consumer(args.artifact).validate_profile(profile, ROOT)
    profile['review_subject_sha256'] = common.review_subject(profile)
    write(args.draft, profile)
    return profile


def assemble(args):
    profile = old.read(args.draft)
    current_inputs({'source_inputs': profile['source_inputs']}, args.artifact)
    common.require(profile['review_subject_sha256'] == common.review_subject(profile), 'Prepared subject changed')
    for name, expected in profile['evidence_sha256'].items():
        common.require(old.digest(old.checked(ROOT, name)) == expected, 'Prepared evidence changed after review')
    for name in profile['independent_reviews']:
        review = old.read(old.checked(ROOT, name))
        common.require(review.get('facts_passed') is True and review.get('risk_acceptance') is False
                       and review.get('actor_kind') == 'automated_independent_agent'
                       and review.get('binary_redistribution_approved') is False
                       and review.get('subject_sha256') == profile['review_subject_sha256'],
                       'Actual independent technical review is missing or for another subject')
        profile['evidence_sha256'][name] = old.digest(ROOT / name)
    common.require(len({old.read(ROOT / name)['actor'] for name in profile['independent_reviews']}) >= 2,
                   'Two distinct actual technical reviewers are required')
    profile['status'] = 'verified'
    builder.consumer(args.artifact).validate_profile(profile, ROOT)
    output = ROOT / ('deployment/security/' + args.artifact + '-source-20261112-profile.json')
    write(output, profile)
    builder.consumer(args.artifact).load_profile(ROOT)
    return profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', choices=('minio', 'grafana'), required=True)
    parser.add_argument('--build-dir', type=Path)
    parser.add_argument('--trivy', type=Path)
    parser.add_argument('--prior-trivy', type=Path)
    parser.add_argument('--grype', type=Path)
    parser.add_argument('--sbom', type=Path)
    parser.add_argument('--draft', type=Path, required=True)
    parser.add_argument('--assemble-reviewed', action='store_true')
    args = parser.parse_args(argv)
    if not args.assemble_reviewed and (args.build_dir is None or args.trivy is None or args.prior_trivy is None):
        parser.error('Actual build directory and both complete raw Trivy reports are required for preparation')
    profile = assemble(args) if args.assemble_reviewed else create(args)
    print(json.dumps({'passed': args.assemble_reviewed, 'prepared': True,
                      'subject_sha256': profile['review_subject_sha256'],
                      'binary_redistribution_approved': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
