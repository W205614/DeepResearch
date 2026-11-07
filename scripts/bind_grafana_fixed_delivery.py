"""After explicit approval/publication, prepare only objective delivery-data pins."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_release_20261107 as api  # noqa: E402
from scripts import fetch_grafana_candidate as fetch  # noqa: E402
from scripts import grafana_release_20261107_delivery as delivery  # noqa: E402


def pin_patch(source, transport_bytes, publication_bytes, request):
    transport = api.strict_json(transport_bytes)
    publication = api.strict_json(publication_bytes)
    binding, proof = request['binding'], publication['proof']
    transport_sha = hashlib.sha256(transport_bytes).hexdigest()
    publication_sha = hashlib.sha256(publication_bytes).hexdigest()
    profile = fetch.LoaderProfile(api, api.TRANSPORT_PATH, None, api.RELEASE_TAG, api.APPROVED_TAG, api.SCAN_TAG)
    fetch.validate_transport(transport, request['binding'], profile=profile)
    api.require(publication['published'] is True and publication['request_sha256'] == api.REQUEST_SHA256
        and publication['subject'] == api.subject_for(request['binding'])
        and publication['release_id'] == transport['release_id'] and publication['release_tag'] == api.RELEASE_TAG
        and publication['transport_sha256'] == transport_sha
        and publication['actual_tag_commit'] == api.RELEASE_SOURCE_COMMIT
        and all(proof[k] == binding[k] for k in ('index_digest', 'platform_manifest_digest',
            'config_digest', 'archive_sha256', 'binary_sha256'))
        and proof['recomputed_diff_ids'] == binding['diff_ids'],
        'Only observed exact publication metadata can fill delivery pins')
    result = source
    for name, digest in [('TRANSPORT_SHA256', transport_sha), ('PUBLICATION_SHA256', publication_sha)]:
        before, after = name + ' = None', name + ' = ' + repr(digest)
        api.require(result.count(before) == 1, 'Expected unfilled data pin is absent or already changed')
        result = result.replace(before, after, 1)
    patch = ''.join(difflib.unified_diff(source.splitlines(True), result.splitlines(True),
        fromfile='a/scripts/check_grafana_release_20261107.py', tofile='b/scripts/check_grafana_release_20261107.py'))
    for path, raw in [(api.TRANSPORT_PATH.as_posix(), transport_bytes),
                      (api.PUBLICATION_PATH.as_posix(), publication_bytes)]:
        api.require(b'\r\n' not in raw, 'New delivery JSON must retain physical LF bytes')
        patch += ''.join(difflib.unified_diff([], raw.decode('utf-8').splitlines(True), fromfile='/dev/null', tofile='b/' + path))
    return patch, transport


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--transport', type=Path, required=True)
    parser.add_argument('--publication', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    request = api.load_request(ROOT)
    api.validate_authorization(api.read_json(ROOT / api.AUTH_PATH), request)
    api.require(not args.output.exists() and args.output.resolve().is_relative_to((ROOT / '.cache/eval').resolve()),
                'Use a new data-pin patch under .cache/eval')
    patch, transport = pin_patch((ROOT / 'scripts/check_grafana_release_20261107.py').read_text(encoding='utf-8'),
                                 args.transport.read_bytes(), args.publication.read_bytes(), request)
    profile = fetch.LoaderProfile(api, api.TRANSPORT_PATH, None, api.RELEASE_TAG, api.APPROVED_TAG, api.SCAN_TAG)
    live = fetch.verify_publication(transport, request['binding'], profile=profile)
    client = delivery.authorized_client(os.environ.get('GITHUB_TOKEN'))
    api.require(delivery.tag_commit(client, api.RELEASE_TAG) == api.RELEASE_SOURCE_COMMIT,
                'Live published tag differs from the actual reviewed build source')
    api.validate_authorization(api.read_json(ROOT / api.AUTH_PATH), api.load_request(ROOT))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(patch.encode('utf-8'))
    print(json.dumps({'data_patch_only': True, 'output': str(args.output), 'live': live,
                      'active_selector_modified': False, 'human_authorization_written': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
