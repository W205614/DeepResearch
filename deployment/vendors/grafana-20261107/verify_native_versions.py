"""Check actual rebuilt ELF dependency versions without applying CVE dispositions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

FIXED = {"github.com/andybalholm/brotli": "v1.2.7", "github.com/klauspost/compress": "v1.20.3", "golang.org/x/net": "v0.62.0"}


def verify_dependencies(native, executable):
    found = {}
    for dependency in native.get("Deps", []):
        name = dependency["Path"]
        if name in FIXED:
            if "Replace" in dependency:
                raise ValueError(str(executable) + ": replacement is forbidden for fixed dependency " + name)
            if dependency["Version"] != FIXED[name]:
                raise ValueError(str(executable) + ": unexpected actual " + name + " version " + dependency["Version"])
            found[name] = dependency["Version"]
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--main", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    modules = json.loads(args.inventory.read_bytes())
    paths = [args.root / row["plugin_directory"] / row["binary"] for row in modules]
    if args.main:
        paths.append(args.main)
    results = []
    for path in paths:
        result = subprocess.run(["go", "version", "-m", "-json", str(path)], capture_output=True, text=True, check=True)
        dependencies = verify_dependencies(json.loads(result.stdout), path)
        results.append({"executable": str(path), "fixed_dependencies_present": dependencies})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"actual_native_fixed_versions": results, "vulnerability_dispositions_granted": False, "artifact_approved": False}, indent=2) + "\n")


if __name__ == "__main__":
    main()
