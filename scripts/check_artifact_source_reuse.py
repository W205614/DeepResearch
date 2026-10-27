"""Bind unchanged approved artifacts to an ancestor of the consuming app commit."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import grafana_image_disposition as grafana  # noqa: E402
from scripts import image_disposition as minio  # noqa: E402

ARTIFACT_PUBLICATION_SOURCE = '6bec3f00d883f0de0aac89b29d3a30cd7325fdf3'
RELEASE_TAGS = ('artifact-minio-fixed-20261026', 'artifact-grafana-reviewed-20261026')


def git(root, *args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout


def verify_snapshot(root, expected, *, built_from=ARTIFACT_PUBLICATION_SOURCE, tags=RELEASE_TAGS):
    consumed_by = git(root, 'rev-parse', 'HEAD').decode().strip()
    git(root, 'merge-base', '--is-ancestor', built_from, consumed_by)
    for tag in tags:
        if git(root, 'rev-parse', 'refs/tags/' + tag + '^{commit}').decode().strip() != built_from:
            raise minio.DispositionError('Published artifact tag no longer identifies the fixed ancestor')
    # cat-file --batch preserves original bytes. Git text conversion is not proof
    # of equality for frozen recipes, archives, ELF inventories or approval files.
    specs = [(revision + ':' + name) for name in sorted(expected) for revision in (built_from, consumed_by, '')]
    process = subprocess.run(['git', 'cat-file', '--batch'], cwd=root,
                             input=('\n'.join(specs) + '\n').encode(), capture_output=True, check=True)
    raw, cursor = process.stdout, 0
    for name in sorted(expected):
        for _ in range(3):
            end = raw.find(b'\n', cursor)
            header = raw[cursor:end].split()
            if end < 0 or len(header) != 3 or header[1] != b'blob':
                raise minio.DispositionError('Frozen Git object is missing or is not a blob')
            size = int(header[2])
            cursor = end + 1
            blob = raw[cursor:cursor + size]
            cursor += size + 1
            if len(blob) != size or raw[cursor - 1:cursor] != b'\n' or hashlib.sha256(blob).hexdigest() != expected[name]:
                raise minio.DispositionError('Frozen ancestor/current commit/index bytes differ')
        if minio.file_hash(minio.checked_file(root, name)) != expected[name]:
            raise minio.DispositionError('Frozen working bytes differ')
    if cursor != len(raw):
        raise minio.DispositionError('Unexpected trailing Git object evidence')
    return {'passed': True, 'artifact_publication_source': built_from, 'consumed_by_application_commit': consumed_by,
            'publication_source_is_ancestor': True, 'unchanged_frozen_files': len(expected),
            'raw_exact_comparisons': ['ancestor_commit', 'application_commit', 'index', 'working_tree'],
            'release_tags_unchanged': list(tags), 'artifacts_rebuilt_at_application_commit': False,
            'boundary': 'Source reuse only; live publication, archive/ELF/image identity and fresh scan gates remain required.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        bindings = [minio.load_binding(), grafana.load_binding()]
        expected = {}
        for binding in bindings:
            for name, digest in {**binding['recipe_files'], **binding['evidence_files']}.items():
                if name in expected and expected[name] != digest:
                    raise minio.DispositionError('Artifact source bindings disagree')
                expected[name] = digest
        if len(expected) != 128:
            raise minio.DispositionError('Frozen artifact inventory differs')
        report = verify_snapshot(ROOT, expected)
    except (OSError, ValueError, subprocess.SubprocessError):
        report = {'passed': False, 'mode': 'failed_closed', 'error': 'Artifact ancestor/source reuse proof failed'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__':
    main()
