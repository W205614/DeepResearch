"""Derive a fresh Tempo ELF review draft while preserving the previous exact review."""
from __future__ import annotations

import argparse
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_tempo_app_exact1 as gate  # noqa: E402

HISTORICAL = 'deployment/security/tempo-app1-evidence/data-refresh-20261030/technical-review.json'
EVIDENCE = 'deployment/security/tempo-app1-evidence/otel-refresh-20261115'
OLD_REVIEW_SHA256 = 'b5b2e58748d4083750d71438fc81de2c2ec62ac1271a59b652f3b3d07575d434'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + '\n').encode())


def generate(args):
    historical = ROOT / HISTORICAL
    if not historical.exists():
        raw = subprocess.check_output(['git', 'show', '507461afa04c656e15dbf3a180cedaa9cb7e5f62:'
                                       'deployment/security/tempo-app1-technical-review.json'], cwd=ROOT)
        if hashlib.sha256(raw).hexdigest() != OLD_REVIEW_SHA256:
            raise ValueError('Exact previous Tempo review is unavailable')
        historical.write_bytes(raw)
    if gate.file_hash(historical) != OLD_REVIEW_SHA256:
        raise ValueError('Historical Tempo review changed')
    old = gate.strict_json(historical.read_bytes())
    for name, expected in old['evidence_files'].items():
        if gate.file_hash(ROOT / name) != expected:
            raise ValueError('Historical Tempo proof changed: ' + name)
    receipt = json.loads((ROOT / 'deployment/security/tempo-app1-evidence/source-receipt.json').read_bytes())
    for item in receipt['sources']:
        relative = item.get('stored_path', 'upstream-source/' + item['path'])
        data = (ROOT / 'deployment/security/tempo-app1-evidence' / relative).read_bytes()
        if (hashlib.sha256(data).hexdigest() != item['sha256']
                or hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() != item['git_blob']):
            raise ValueError('Original fixed Tempo upstream source blob differs')
    material = gate.strict_json((args.materials_dir / 'materials.json').read_bytes())
    inspection = gate.strict_json((args.materials_dir / 'image-inspect.json').read_bytes())
    native = gate.strict_json((args.materials_dir / 'native-buildinfo.json').read_bytes())
    actual = subprocess.check_output([args.go, 'version', '-m', '-json',
                                      str(args.materials_dir / 'tempo.elf')])
    if gate.strict_json(actual) != native or native['GoVersion'] != 'go1.27.4':
        raise ValueError('Actual Tempo ELF must be rebuilt with the fixed compiler')
    report = gate.strict_json(args.scan.read_bytes())
    if 'CVE-2026-97054' in args.scan.read_text(encoding='utf-8') or 'CVE-2026-128085' in args.scan.read_text(encoding='utf-8'):
        raise ValueError('The Go or OpenTelemetry security update did not remove the target findings')
    if material['elf_sha256'] != gate.file_hash(args.materials_dir / 'tempo.elf'):
        raise ValueError('Tempo ELF and material hash differ')
    build_log = args.build_log.read_text(encoding='utf-8')
    if ('--- PASS: TestSSEConfigEncryptionKeyRedacted' not in build_log
            or '=== RUN   TestSSEConfigEncryptionKeyRedacted' not in build_log):
        raise ValueError('The actual upstream encryption-key redaction regression did not pass')
    review = deepcopy(old)
    review.update(actor='PENDING_INDEPENDENT_REVIEW', facts_passed=False,
                  recorded_at=None, source_commit=None)
    review['scope'].update(elf_sha256=material['elf_sha256'], elf_bytes=material['elf_bytes'])
    subject = review['fixed_subject']
    subject['input_sha256'] = {name: gate.file_hash(ROOT / name) for name in subject['input_sha256']}
    subject.update(native_buildinfo_sha256=gate.file_hash(args.materials_dir / 'native-buildinfo.json'),
                   native_text_body_sha256=hashlib.sha256((args.materials_dir / 'native-buildinfo.txt').read_bytes().split(b'\n', 1)[1]).hexdigest(),
                   runtime_version_sha256=gate.file_hash(args.materials_dir / 'runtime-version.txt'),
                   native_dependency_count=len(native['Deps']), go_version='go1.27.4')
    records = {r['Type']: r for r in report['Results']}
    packages = records['gobinary']['Packages']
    subject['go_package_count'] = len(packages)
    subject['go_package_layer_index'] = {
        p['Name']: inspection['RootFS']['Layers'].index(p['Layer']['DiffID']) for p in packages}
    if subject['config'] != inspection['Config'] or subject['base_diff_ids'] != inspection['RootFS']['Layers'][:-3]:
        raise ValueError('Tempo base runtime or configuration changed')
    review['evidence_files'][HISTORICAL] = OLD_REVIEW_SHA256
    evidence = ROOT / EVIDENCE
    evidence.mkdir(parents=True, exist_ok=True)
    for source in args.materials_dir.iterdir():
        if source.name == 'tempo.elf' or not source.is_file():
            continue
        destination = evidence / source.name
        destination.write_bytes(source.read_bytes())
        review['evidence_files'][destination.relative_to(ROOT).as_posix()] = gate.file_hash(destination)
    for source, name in ((args.scan, 'raw-trivy.json.gz'), (args.build_log, 'build.log.gz')):
        destination = evidence / name
        destination.write_bytes(gzip.compress(source.read_bytes(), mtime=0))
        review['evidence_files'][destination.relative_to(ROOT).as_posix()] = gate.file_hash(destination)
    review['review_sample'] = {
        'native_rederived_from_actual_elf': True,
        'source': 'Actual source-built image and unmodified full Trivy scan; candidate, not OCI publication approval',
        'image_id': inspection['Id'], 'elf_sha256': material['elf_sha256'],
        'upstream_sse_key_redaction_regression_passed': True,
    }
    basis = review['technical_basis']
    basis[3] = ('Pinned dskit Secret.MarshalYAML returns a redaction marker rather than the plaintext key. '
                'The actual Go 1.27.4 source build executed the upstream '
                'TestSSEConfigEncryptionKeyRedacted regression and it passed; no business end-to-end test is claimed.')
    basis[4] = ('Actual source-built ELF SHA256 was independently recomputed and native go version -m -json '
                'was rederived locally from those bytes, matching the collected native JSON. Main VCS revision '
                'remains the fixed f317720e; the pinned source checkout has only the locked module overlays.')
    basis[5] = ('Actual runtime -version reports 2.10.10/f317720e/go1.27.4. Native module version remains '
                'the true v0 pseudo-version; no application or Go module version is rewritten.')
    basis[6] = (f'Raw scanner Go inventory equals all {len(native["Deps"])} effective native dependency '
                'path/version pairs plus main and stdlib. Fresh scan ImageID and DiffIDs equal the '
                'actual collected image inspection and materials.')
    review['otel_compiler_refresh_20261115'] = {
        'prior_review': HISTORICAL, 'prior_review_sha256': OLD_REVIEW_SHA256,
        'upstream_source_and_cve_fix_evidence_unchanged': True,
        'actual_compiler': 'go1.27.4', 'otel_family': 'v1.47.0',
        'native_and_full_inventory_rederived': True,
        'raw_report_modified': False,
        'boundary': 'Only the original CVE-2026-28377 HIGH finding is technically scoped. New HIGH/CRITICAL or secrets remain blocked.',
    }
    write(args.output, review)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('materials-dir', 'scan', 'build-log', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--go', required=True)
    generate(parser.parse_args())


if __name__ == '__main__':
    main()
