"""Derive the exact Backend transport continuation from an actual target-stage export."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_backend_busybox_20261113 as gate  # noqa: E402


def file_sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def derive(out_tar, inspection_path, build_log, original_recipe, *, root=ROOT):
    retained_raw = (root / gate.PROFILE).read_bytes()
    gate.require(gate.sha(retained_raw) == gate.RETAINED_PROFILE_SHA256, 'Original profile changed')
    retained = json.loads(retained_raw)
    before = original_recipe.read_bytes()
    gate.require(len(before) == 4819 and gate.sha(before) == retained['source_inputs'][gate.RECIPE],
                 'Original complete recipe differs')
    after = before
    for asset in gate.transport_assets():
        source, destination = asset['original_url'].encode(), asset['url'].encode()
        gate.require(after.count(source) == 1, 'Original URL is not unique')
        after = after.replace(source, destination)
    gate.require(after == (root / gate.RECIPE).read_bytes(), 'Current recipe changes more than four URLs')
    files = gate.archive_files(out_tar.read_bytes())
    expected_legal = {**retained['legal_source_files'],
                      gate.LEGAL_RECIPE: {'bytes': len(after), 'sha256': gate.sha(after)}}
    gate.require(files == {**expected_legal, 'busybox': retained['payload']['/bin/busybox']},
                 'Actual target output differs beyond its complete integration recipe')
    with tarfile.open(out_tar) as archive:
        recipes = [member for member in archive if member.name.removeprefix('./') == gate.LEGAL_RECIPE]
        gate.require(len(recipes) == 1 and archive.extractfile(recipes[0]).read() == after,
                     'Actual installed target recipe is not the current exact source')
    inspection = json.loads(inspection_path.read_bytes())
    gate.require(inspection['Os'] == 'linux' and inspection['Architecture'] == 'amd64'
                 and inspection['Id'].startswith('sha256:') and build_log.stat().st_size > 0,
                 'Actual target identity or build log is missing')
    profile = deepcopy(retained)
    profile.update(schema=gate.TRANSPORT_SCHEMA,
                   scope='Exact four-URL transport continuation; both complete retained and rebuilt source closures.')
    profile['source_inputs'].update({gate.RECIPE: gate.sha(after),
                                    gate.CHECKER: file_sha(root / gate.CHECKER),
                                    gate.PRODUCER: file_sha(root / gate.PRODUCER)})
    profile['evidence_sha256'][gate.PROFILE.as_posix()] = gate.RETAINED_PROFILE_SHA256
    profile['transport_continuation'] = {'retained_profile_sha256': gate.RETAINED_PROFILE_SHA256,
                                        'original_recipe_utf8': before.decode('utf-8'),
                                        'assets': gate.transport_assets()}
    profile['legal_source_variants'] = {'retained': retained['legal_source_files'], 'rebuilt': expected_legal}
    profile['collection'] = {'schema': 'actual-backend-busybox-target-export-v1',
                             'target': 'backend-busybox-fix', 'actual_image_id': inspection['Id'],
                             'os': inspection['Os'], 'architecture': inspection['Architecture'],
                             'out_tar_sha256': file_sha(out_tar), 'image_inspect_sha256': file_sha(inspection_path),
                             'build_log_sha256': file_sha(build_log),
                             'actual_busybox': files['busybox'], 'legal_regular_files': len(expected_legal),
                             'changed_legal_files': [gate.LEGAL_RECIPE],
                             'new_java_compilation_or_jre_scan_claimed': False,
                             'independent_review_claimed': False}
    return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-out-tar', type=Path, required=True)
    parser.add_argument('--image-inspect', type=Path, required=True)
    parser.add_argument('--build-log', type=Path, required=True)
    parser.add_argument('--original-recipe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    profile = derive(args.target_out_tar, args.image_inspect, args.build_log, args.original_recipe)
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(profile, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'derived': True, 'profile_sha256': file_sha(args.output),
                      'legal_source_variants': {key: len(value) for key, value in profile['legal_source_variants'].items()}}))


if __name__ == '__main__':
    main()
