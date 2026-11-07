"""Collect full reports only after new-subject authorization, without deployment."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import check_grafana_release_20261107 as api  # noqa: E402
from scripts import scan_grafana_candidate as common  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=api.SCAN_TAG)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--tool-dir', type=Path, default=api.ROOT / '.cache/native-jvm/tools')
    parser.add_argument('--db-cache', type=Path, default=api.ROOT / '.cache/native-jvm/grype-db')
    args = parser.parse_args(argv)
    result = common.collect(args, consumer=api)
    print(json.dumps({key: result[key] for key in
        ('operational_success', 'security_gate_claim', 'actual_image_id', 'error_type', 'error') if key in result}))
    return 0 if result['operational_success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
