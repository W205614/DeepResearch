"""Collect an unapproved Grafana build for review; this grants no disposition."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.check_native_jvm import executable, prepare_database, run  # noqa: E402


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def native_evidence(image, scan, destination):
    targets = sorted({row['Target'] for row in scan['Results'] if row.get('Type') == 'gobinary'})
    if 'usr/share/grafana/bin/grafana' not in targets:
        raise ValueError('Actual main ELF is absent from the complete candidate scan')
    container = run(['docker', 'create', image]).decode().strip()
    if not re.fullmatch('[0-9a-f]{64}', container):
        raise ValueError('Unexpected candidate container identity')
    records = {}
    try:
        build_evidence = destination / 'build-evidence'
        build_evidence.mkdir()
        run(['docker', 'cp', container + ':/usr/share/deepresearch/grafana-candidate/.', str(build_evidence)])
        with tempfile.TemporaryDirectory(prefix='grafana-review-') as temporary:
            for index, target in enumerate(targets):
                relative = PurePosixPath(target)
                if relative.is_absolute() or '..' in relative.parts or '\\' in target:
                    raise ValueError('Candidate scanner ELF path is not a safe relative path')
                path = Path(temporary) / (str(index) + '.elf')
                run(['docker', 'cp', container + ':/' + target, str(path)])
                information = run(['go', 'version', '-m', str(path)]).decode('utf-8')
                info = destination / ('elf-' + str(index) + '-buildinfo.txt')
                info.write_text(information, encoding='utf-8')
                records[target] = {'elf_sha256': digest(path), 'elf_bytes': path.stat().st_size,
                                   'actual_native_buildinfo': info.name, 'buildinfo_sha256': digest(info)}
                if target == 'usr/share/grafana/bin/grafana':
                    for module, version in [('github.com/go-jose/go-jose/v4', 'v4.1.6'),
                                             ('golang.org/x/net', 'v0.62.0')]:
                        if not re.search(r'^\s*dep\s+' + re.escape(module) + r'\s+' + re.escape(version) + r'(?:\s|$)',
                                         information, re.MULTILINE):
                            raise ValueError('Candidate main ELF does not contain the required fixed dependency')
    finally:
        # Only the never-started container created above belongs to this collector.
        actual = json.loads(run(['docker', 'inspect', container]))[0]
        if actual['State']['Status'] != 'created' or actual['Image'] != image:
            raise ValueError('Candidate inspection container state or image unexpectedly changed')
        run(['docker', 'rm', container])
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--scan', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    result = {'operational_success': False, 'security_gate_claim': False,
              'artifact_approved': False, 'daily_deployment_performed': False}
    try:
        actual = json.loads(run(['docker', 'image', 'inspect', args.image]))[0]
        (args.output_dir / 'image-inspect.json').write_text(json.dumps(actual, indent=2), encoding='utf-8')
        scan = json.loads(args.scan.read_bytes())
        if (scan.get('SchemaVersion') != 2 or not scan.get('Results')
                or scan.get('Metadata', {}).get('ImageID') != actual['Id']
                or scan['Metadata'].get('DiffIDs') != actual['RootFS']['Layers']):
            raise ValueError('Complete candidate scan and actual loaded image identities differ')
        result['native_elfs'] = native_evidence(actual['Id'], scan, args.output_dir)
        lock = json.loads((ROOT / 'deployment/security/native-jvm-tools.json').read_bytes())
        tool_dir, database_dir = ROOT / '.cache/native-jvm/tools', ROOT / '.cache/native-jvm/grype-db'
        syft = executable('syft', lock, tool_dir, tool_dir)
        grype = executable('grype', lock, tool_dir, tool_dir)
        env = {key: value for key, value in os.environ.items() if not key.startswith(('SYFT_', 'GRYPE_'))}
        env.update(SYFT_CHECK_FOR_APP_UPDATE='false', GRYPE_CHECK_FOR_APP_UPDATE='false')
        result['database'] = {}
        prepared = prepare_database(grype, lock['database'], database_dir, args.output_dir, env, result['database'])
        syconfig, grconfig = args.output_dir / 'syft-config.json', args.output_dir / 'grype-config.json'
        syconfig.write_text(json.dumps({'exclude': [], 'check-for-app-update': False}), encoding='utf-8')
        grconfig.write_text(json.dumps({'ignore': [], 'only-fixed': False, 'only-notfixed': False,
                                       'check-for-app-update': False}), encoding='utf-8')
        sbom, findings = args.output_dir / 'full-syft.json', args.output_dir / 'full-grype.json'
        run([syft, '--config', syconfig, 'scan', 'docker:' + actual['Id'],
             '-o', 'syft-json=' + str(sbom), '--quiet'], env=prepared.env)
        run([grype, '--config', grconfig, 'sbom:' + str(sbom), '-o', 'json',
             '--file', findings, '--quiet'], env=prepared.env)
        prepared.assert_unchanged('after-candidate-full-scan')
        result.update(operational_success=True, actual_image_id=actual['Id'],
                      archive_sha256=digest(args.archive), archive_bytes=args.archive.stat().st_size,
                      raw_trivy_sha256=digest(args.scan), raw_syft_sha256=digest(sbom),
                      raw_grype_sha256=digest(findings),
                      next_step='Independent exact-ELF/source/finding review and renewed subject authorization.')
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        result.update(error_type=type(error).__name__, error=str(error)[:300])
    (args.output_dir / 'candidate-materials.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: result[key] for key in ('operational_success', 'security_gate_claim', 'artifact_approved')}))
    raise SystemExit(0 if result['operational_success'] else 1)


if __name__ == '__main__':
    main()
