"""Load only the separately authorized fixed20261107 namespace."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import check_grafana_release_20261107 as api  # noqa: E402
from scripts import fetch_grafana_candidate as common  # noqa: E402


def loader_profile():
    return common.LoaderProfile(api, api.TRANSPORT_PATH, api.TRANSPORT_SHA256,
                                api.RELEASE_TAG, api.APPROVED_TAG, api.SCAN_TAG)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--tag', default=api.APPROVED_TAG)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    forwarded = ['--tag', args.tag]
    for field in ('archive', 'output'):
        value = getattr(args, field)
        if value is not None:
            forwarded.extend(['--' + field, str(value)])
    return common.main(forwarded, profile_override=loader_profile(), profile_name=api.PROFILE,
                       cache_archive_name='approved-grafana-fixed-20261107.oci.tar')


if __name__ == '__main__':
    raise SystemExit(main())
