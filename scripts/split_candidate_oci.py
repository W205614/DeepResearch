"""Create bounded verified transport parts; never grant an image disposition."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    expected_bytes = args.archive.stat().st_size
    if not 0 < expected_bytes <= 2 * 1024**3:
        raise ValueError('Candidate archive must fit the eight bounded upload groups')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    whole, parts = hashlib.sha256(), []
    with args.archive.open('rb') as source:
        while block := source.read(64 * 1024**2):
            group = f'group-{len(parts) // 4:03d}'
            (args.output_dir / group).mkdir(exist_ok=True)
            name = f'{group}/part-{len(parts):03d}.bin'
            (args.output_dir / name).write_bytes(block)
            whole.update(block)
            parts.append({'name': name, 'bytes': len(block), 'sha256': hashlib.sha256(block).hexdigest()})
    if sum(part['bytes'] for part in parts) != expected_bytes or len(parts) > 32:
        raise ValueError('Candidate archive changed while creating its transport parts')
    manifest = {'schema': 'candidate-oci-parts-v1', 'archive_bytes': sum(p['bytes'] for p in parts),
                'archive_sha256': whole.hexdigest(), 'parts': parts,
                'artifact_prefix': 'grafana-candidate-oci-' + os.environ.get('GITHUB_SHA', 'local'),
                'groups': sorted({part['name'].split('/')[0] for part in parts}), 'artifact_approved': False,
                'security_gate_claim': False}
    (args.output_dir / 'parts-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'archive_sha256': whole.hexdigest(), 'parts': len(parts), 'artifact_approved': False}))


if __name__ == '__main__':
    main()
