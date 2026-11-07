"""Convert an explicit provider backup-status record to node_exporter textfile metrics.

No cloud calls, backup scheduling, or assertions about successful restore drills.
The provider observation timestamp is retained so stale input cannot look fresh.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import time


def render(record: dict, *, clock=time.time) -> str:
    def timestamp(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Backup timestamps must be finite epoch seconds")
        if not 0 <= value <= clock() + 300:
            raise ValueError("Backup timestamp is outside the observed time range")
        return value

    if record.get("version") != 1:
        raise ValueError("Expected backup status record version 1")
    observed = timestamp(record.get("observed_at"))
    resources = record.get("resources")
    if not isinstance(resources, list) or not 1 <= len(resources) <= 32:
        raise ValueError("Configure one to thirty-two required resource aliases")
    descriptions = {"required": "Resource explicitly required by the managed backup policy",
                    "observed_timestamp_seconds": "Provider backup status observation time in epoch seconds",
                    "last_success_timestamp_seconds": "Last successful provider backup time in epoch seconds",
                    "rpo_target_seconds": "Configured maximum backup age in seconds"}
    lines = []
    for name, description in descriptions.items():
        lines += [f"# HELP deepresearch_cloud_backup_{name} {description}",
                  f"# TYPE deepresearch_cloud_backup_{name} gauge"]
    seen = set()
    for resource in resources:
        if not isinstance(resource, dict):
            raise ValueError("Each backup resource must be an object")
        alias = resource.get("resource", "")
        if not isinstance(alias, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", alias) or alias in seen:
            raise ValueError("Backup resource aliases must be unique non-secret identifiers")
        seen.add(alias)
        success = timestamp(resource.get("last_success_at"))
        if success > observed:
            raise ValueError("Backup success cannot be newer than the provider observation")
        rpo = resource.get("rpo_seconds")
        if isinstance(rpo, bool) or not isinstance(rpo, int) or not 60 <= rpo <= 31536000:
            raise ValueError("RPO must be an integer from 60 seconds to one year")
        label = f'{{resource="{alias}"}}'
        for name, value in (("required", 1), ("observed_timestamp_seconds", observed),
                            ("last_success_timestamp_seconds", success), ("rpo_target_seconds", rpo)):
            lines.append(f"deepresearch_cloud_backup_{name}{label} {value}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    content = render(json.loads(args.input.read_text(encoding="utf-8")))
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(args.output)


if __name__ == "__main__":
    main()
