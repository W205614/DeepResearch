"""Derive the 20261114 otel-v1.47.0 review draft from the actual built image and prior facts."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_build_20261112 as old  # noqa: E402
from scripts import check_grafana_source_build_20261114 as current  # noqa: E402
from scripts.check_grafana_source_build_20261110 import checked  # noqa: E402
from scripts import go_otel_rebuild_20261114 as common  # noqa: E402
from scripts.retained_source_review_20261112 import snapshot  # noqa: E402

EVIDENCE = 'deployment/security/grafana-source-20261114-evidence/'
MODULES = 'deployment/vendors/grafana-20261107/plugin-modules.json'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def relative(path):
    return Path(path).resolve().relative_to(ROOT).as_posix()


def native_evidence_map():
    """Build the target -> {elf, prior_elf, native, prior_native, graph, prior_graph} map."""
    # Read the 20261112 profile bytes directly (not load_profile, which fail-closes
    # on the changed lock bytes that this new generation is specifically migrating).
    prior = json.loads((ROOT / 'deployment/security/grafana-source-20261112-profile.json').read_bytes())
    prior_expected = prior['expected']['native_sha256']
    current_native = json.loads(Path('.cache/otel-bump/build-20261114/native-evidence/native-sha256.json').read_text())
    modules = old.read(ROOT / MODULES)
    name_of = {'usr/share/grafana/bin/grafana': 'main'}
    for row in modules:
        name_of['usr/share/grafana/data/plugins-bundled/' + row['plugin_directory'] + '/' + row['binary']] = row['plugin_directory']
    # prior native/graph paths from the 20261112 profile
    prior_ev = {}
    for target, row in prior['native_evidence'].items():
        prior_ev[target] = row
    result = {}
    for target, elf in current_native.items():
        name = name_of[target]
        prior_row = prior_ev[target]
        result[target] = {
            'elf_sha256': elf,
            'prior_elf_sha256': prior_expected[target],
            'native': EVIDENCE + name + '-native.json',
            'prior_native': prior_row['native'],
            'graph': EVIDENCE + name + '-graph.json.gz',
            'prior_graph': prior_row['graph'],
        }
    return result, prior


def build_profile(prior, native_evidence):
    prior_expected = prior['expected']['native_sha256']
    native = {t: r['elf_sha256'] for t, r in native_evidence.items()}
    # runtime manifest
    runtime_current = json.loads(gzip.decompress((ROOT / (EVIDENCE + 'runtime-manifest.json.gz')).read_bytes()))
    runtime_prior = json.loads(gzip.decompress((ROOT / prior['runtime_evidence']['current']).read_bytes()))
    runtime_manifest_sha256 = old.manifest_hash(runtime_current)
    runtime_config = prior['expected']['runtime_config']

    profile = {
        'schema': 'go-otel-source-profile-20261114-v1',
        'status': 'prepared',
        'profile': 'grafana-source-20261114',
        'image': 'deepresearch-grafana-source:20261114',
        'dockerfile': 'deployment/docker/Dockerfile.grafana-20261114',
        'context': '.',
        'source_inputs': {},  # filled below
        'expected': {
            'native_sha256': native,
            'runtime_manifest_sha256': runtime_manifest_sha256,
            'runtime_config': runtime_config,
            'trivy_inventory_sha256': prior['expected']['trivy_inventory_sha256'],
            'syft_types': prior['expected']['syft_types'],
        },
        'reviewed_findings': prior['reviewed_findings'],
        'historical_review_source': common.SOURCE,
        'old_binary_approval_inherited': False,
        'binary_redistribution_approved': False,
        'native_evidence': native_evidence,
        'otel_fix': common.otel_fix(),
        'prior_runtime_manifest_sha256': prior['expected']['runtime_manifest_sha256'],
        'runtime_evidence': {
            'prior': 'deployment/security/grafana-source-20261112-evidence/runtime-manifest.json.gz',
            'current': EVIDENCE + 'runtime-manifest.json.gz',
        },
        'scan_migration': {
            # The otel-v1.47.0 rebuild does not change the image-level Trivy scan:
            # otel is statically linked into the compiled binaries and never appears
            # as an image-scan row. So the 20261114 image scan must equal the
            # already-fixed (Go 1.27.4) 20261112 image scan byte-for-byte.
            'prior': 'deployment/security/grafana-source-20261112-evidence/raw-trivy.json.gz',
            'current': EVIDENCE + 'raw-trivy.json.gz',
        },
        'independent_reviews': [
            EVIDENCE + 'review-root.json',
            EVIDENCE + 'review-otel-provenance.json',
        ],
    }
    return profile


def source_inputs_for_20261114(prior):
    """Inherit prior source_inputs, but swap the build script and add the 20261114 recipe."""
    names = dict(prior['source_inputs'])
    # Remove the old Dockerfile and the superseded stdlib build script.
    names.pop('deployment/docker/Dockerfile.grafana-20261112', None)
    names.pop('deployment/docker/Dockerfile.grafana-20261112.dockerignore', None)
    names.pop('scripts/build_go_stdlib_security_20261112.py', None)
    names['deployment/docker/Dockerfile.grafana-20261114'] = old.digest(ROOT / 'deployment/docker/Dockerfile.grafana-20261114')
    names['deployment/docker/Dockerfile.grafana-20261114.dockerignore'] = old.digest(ROOT / 'deployment/docker/Dockerfile.grafana-20261114.dockerignore')
    names['scripts/build_go_otel_security_20261114.py'] = old.digest(ROOT / 'scripts/build_go_otel_security_20261114.py')
    names['scripts/go_otel_rebuild_20261114.py'] = old.digest(ROOT / 'scripts/go_otel_rebuild_20261114.py')
    names['scripts/check_grafana_source_build_20261114.py'] = old.digest(ROOT / 'scripts/check_grafana_source_build_20261114.py')
    # Re-hash all current files (the changed go.mod/go.sum and .gitattributes get their new bytes).
    result = {}
    for name in sorted(names):
        result[name] = old.digest(checked(ROOT, name))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', type=Path, required=True)
    parser.add_argument('--assemble-reviewed', action='store_true')
    args = parser.parse_args(argv)

    native_evidence, prior = native_evidence_map()
    profile = build_profile(prior, native_evidence)
    profile['source_inputs'] = source_inputs_for_20261114(prior)

    if not args.assemble_reviewed:
        # Compute migration facts, runtime changes, scan migration facts.
        facts = {}
        for target, row in native_evidence.items():
            facts[target] = {
                'elf_sha256': row['elf_sha256'],
                'prior_elf_sha256': row['prior_elf_sha256'],
                **common.compare_native(old.read(ROOT / row['native']), old.read(ROOT / row['prior_native'])),
                **common.compare_graphs(gzip.decompress((ROOT / row['graph']).read_bytes()),
                                        gzip.decompress((ROOT / row['prior_graph']).read_bytes())),
            }
        profile['migration_facts'] = facts
        before = {r['path']: r for r in json.loads(gzip.decompress((ROOT / profile['runtime_evidence']['prior']).read_bytes()))}
        after = {r['path']: r for r in json.loads(gzip.decompress((ROOT / profile['runtime_evidence']['current']).read_bytes()))}
        profile['runtime_changes'] = [{'path': name, 'before': before.get(name), 'after': after.get(name)}
                                      for name in sorted(before.keys() | after.keys()) if before.get(name) != after.get(name)]
        # Compute evidence_sha256 over all referenced files (needed before scan migration check).
        files = {relative(p) for p in (ROOT / EVIDENCE).rglob('*') if p.is_file() and p.name not in {'review-root.json', 'review-otel-provenance.json'}}
        files.add(profile['runtime_evidence']['prior'])
        files.add(profile['scan_migration']['prior'])
        files.add(profile['scan_migration']['current'])
        for row in native_evidence.values():
            files.update(row[name] for name in ('native', 'prior_native', 'graph', 'prior_graph'))
        files.update(profile['source_inputs'].keys())
        files.update({'scripts/go_otel_rebuild_20261114.py', 'scripts/check_grafana_source_build_20261114.py',
                      'scripts/check_grafana_source_scan_20261114.py', 'scripts/scan_grafana_source_20261114.py',
                      'scripts/prepare_go_otel_profiles_20261114.py',
                      'deployment/security/grafana-source-20261112-profile.json',
                      'scripts/retained_source_review_20261112.py'})
        profile['evidence_sha256'] = {name: old.digest(checked(ROOT, name)) for name in sorted(files)}
        profile['scan_migration_facts'] = common.verify_scan_migration(profile, ROOT)
        profile['review_subject_sha256'] = common.review_subject(profile)
        write(args.draft, profile)
        print(json.dumps({'prepared': True, 'subject_sha256': profile['review_subject_sha256'],
                          'migration_facts': len(facts), 'runtime_changes': len(profile['runtime_changes'])}))
        return 0

    # assemble-reviewed: verify reviews and mark verified.
    profile = old.read(args.draft)
    common.require(profile['review_subject_sha256'] == common.review_subject(profile), 'Prepared subject changed')
    for name, expected in profile['evidence_sha256'].items():
        common.require(old.digest(checked(ROOT, name)) == expected, 'Prepared evidence changed after review')
    for name in profile['independent_reviews']:
        review = old.read(checked(ROOT, name))
        common.require(review.get('facts_passed') is True and review.get('risk_acceptance') is False
                       and review.get('actor_kind') == 'automated_independent_agent'
                       and review.get('binary_redistribution_approved') is False
                       and review.get('subject_sha256') == profile['review_subject_sha256'],
                       'Actual independent technical review is missing or for another subject')
        profile['evidence_sha256'][name] = old.digest(ROOT / name)
    common.require(len({old.read(ROOT / name)['actor'] for name in profile['independent_reviews']}) >= 2,
                   'Two distinct actual technical reviewers are required')
    profile['status'] = 'verified'
    current.validate_profile(profile, ROOT)
    output = ROOT / 'deployment/security/grafana-source-20261114-profile.json'
    write(output, profile)
    current.load_profile(ROOT)
    print(json.dumps({'verified': True, 'subject_sha256': profile['review_subject_sha256']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
