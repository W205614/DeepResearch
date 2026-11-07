"""Publish the separately authorized existing local OCI, never rebuild or deploy."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import check_grafana_release_20261107 as api  # noqa: E402
from scripts import grafana_release_20261107_delivery as delivery  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--release-id', type=int)
    parser.add_argument('--prepare', action='store_true')
    args = parser.parse_args(argv)
    if args.prepare:
        result = delivery.prepare(api, args.archive)
    else:
        request = api.load_request(api.ROOT)
        api.validate_authorization(api.read_json(api.ROOT / api.AUTH_PATH), request)
        delivery.require(type(args.release_id) is int and args.release_id > 0,
                         'Supply the real exact draft release ID; it cannot be invented during preparation')
        transport, publication = delivery.publish(api, delivery.authorized_client(os.environ.get('GITHUB_TOKEN')),
            args.archive, args.output_dir, release_id=args.release_id)
        result = {'published': True, 'request_sha256': api.REQUEST_SHA256,
                  'archive_sha256': transport['asset_sha256'], 'publication': publication}
    print(json.dumps(result))
    return 0 if result.get('published') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
