"""Generate a conditional selector binding only after real exact OCI publication."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import check_grafana_active_source_retirement as gate  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / gate.MAINTENANCE_REVIEW_PATH)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    gate.require(output.is_relative_to(ROOT.resolve())
                 and not output.exists() and not args.output.is_symlink(),
                 'Use a new distinct workspace selector binding file')
    facts = gate.maintenance_selection_facts(ROOT)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(facts, indent=2) + '\n')
    print(json.dumps({'output': output.relative_to(ROOT).as_posix(),
                      'sha256': gate.approved.file_hash(output),
                      'independent_core_facts_bound': True, 'actual_publication_bound': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
