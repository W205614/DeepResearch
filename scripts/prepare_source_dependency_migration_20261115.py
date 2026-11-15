"""Generate the exact source inventory continuation from a complete raw scan."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_source_scan_20261110 as prior  # noqa: E402
from scripts import check_grafana_source_build_20261110 as build  # noqa: E402
from scripts.retained_source_review_20261112 import snapshot  # noqa: E402
from scripts import source_dependency_migration_20261115 as migration  # noqa: E402


def generate(scan_path):
    scan = json.loads(scan_path.read_bytes())
    observed, counts, _ = prior.source_facts(scan)
    historical = build.load_profile(snapshot(ROOT))
    legacy = historical['expected']['source_scan']
    legacy_keys = set(map(build.finding_key, legacy['reviewed_findings']))
    current_keys = set(map(build.finding_key, observed['findings']))
    prior.require(legacy_keys <= current_keys, 'An old finding changed; separate review is required')
    prior.require(all(value == 1 for value in Counter(map(build.finding_key, observed['findings'])).values()),
                  'Duplicate source findings require separate investigation')
    additional = [r for r in observed['findings'] if build.finding_key(r) not in legacy_keys]
    minio_findings = [row for row in additional if row['target'] == migration.MINIO_TARGET]
    additional = [row for row in additional if row['target'] != migration.MINIO_TARGET]
    prior.require(Counter(map(build.finding_key, minio_findings)) == Counter(migration.MINIO_FINDINGS),
                  'The three exact new MinIO source findings differ; independent review is required')
    prior.require(len(additional) == 29 and all(r['id'] == 'CVE-2026-128085'
        and r['severity'] == 'MEDIUM' and r['target'].startswith('deployment/security/')
        and r['package'].startswith('go.opentelemetry.io/otel/') for r in additional),
        'The expected 29 historical otel findings differ; independent review is required')
    names = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0'))
    # A reviewed working-tree candidate may contain newly added manifests before
    # its first commit. Bind the actual complete scanner targets as well.
    names.update(row[0] for row in observed['scan_targets'])
    for name in list(names):
        if Path(name).name == 'go.mod':
            adjacent = Path(name).with_name('go.sum').as_posix()
            if (ROOT / adjacent).is_file():
                names.add(adjacent)
    inputs = {name: build.digest(build.checked(ROOT, name)) for name in sorted(names)
              if Path(name).name in {'go.mod', 'go.sum', 'uv.lock', 'pom.xml', 'package-lock.json'}}
    proof = {'schema': 'source-dependency-migration-20261115-v1',
        'actor_kind': 'automated_agent_technical_reviewer', 'human_review_claimed': False,
        'raw_scan_sha256': build.digest(scan_path), 'raw_counts': counts,
        'source_inputs': inputs, **observed,
        'contexts_sha256': build.digest(ROOT / migration.CONTEXTS),
        'additional_historical_otel_findings': additional,
        'additional_minio_findings': minio_findings,
        'minio_source_advisories': {
            identifier: {'path': 'deployment/security/source-contexts-20261115/advisories/' + name + '.json',
                         'sha256': build.digest(ROOT / ('deployment/security/source-contexts-20261115/advisories/' + name + '.json')),
                         'url': 'https://vuln.go.dev/ID/' + name + '.json'}
            for identifier, name in [('GO-2026-5932', 'GO-2026-5932'),
                                     ('CVE-2026-78661', 'GO-2026-6860'),
                                     ('CVE-2026-97045', 'GO-2026-6866')]},
        'retained_findings': legacy['reviewed_findings'],
        'technical_basis': 'All 71 prior rows retain their original proofs and are additionally checked against current Grafana native/graphs or current external/standalone/maintained contexts. The 29 historical otel rows map to fixed current Grafana executables. The three new MinIO manifest rows retain their original severities and vulnerable library versions: both actual current Go graphs/native identities prove the officially affected HTML/OpenPGP packages absent. All current source inventories remain exact and no raw report is rewritten.',
        'risk_acceptance': False, 'raw_report_modified': False}
    raw = (json.dumps(proof, indent=2) + '\n').encode()
    (ROOT / migration.REVIEW).write_bytes(raw)
    path = ROOT / 'scripts/source_dependency_migration_20261115.py'
    text = path.read_text(encoding='utf8')
    old = "REVIEW_SHA256 = '" + migration.REVIEW_SHA256 + "'"
    text = text.replace("CONTEXTS_SHA256 = '" + migration.CONTEXTS_SHA256 + "'",
                        "CONTEXTS_SHA256 = '" + build.digest(ROOT / migration.CONTEXTS) + "'")
    path.write_text(text.replace(old, "REVIEW_SHA256 = '" + hashlib.sha256(raw).hexdigest() + "'"),
                    encoding='utf8', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan', required=True, type=Path)
    generate(parser.parse_args().scan)
