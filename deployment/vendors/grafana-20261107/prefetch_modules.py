"""Prefetch official module transport; Go still verifies SumDB and pinned source h1."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import time
import zipfile

import httpx

SOURCE = Path(__file__).resolve().parent
PINNED_TRANSPORT = [("github.com/andybalholm/brotli", "v1.2.7"), ("github.com/klauspost/compress", "v1.20.3"), ("golang.org/x/net", "v0.62.0"),
                    ("github.com/hashicorp/go-plugin", "v1.8.0"),
                    ("cloud.google.com/go/aiplatform", "v1.125.0"),
                    ("github.com/RoaringBitmap/roaring/v2", "v2.14.5"),
                    ("github.com/blevesearch/vellum", "v1.2.0"),
                    ("github.com/grafana/go-mysql-server", "v0.20.2-grafana-4"),
                    ("github.com/grafana/prometheus-alertmanager", "v0.25.1-0.20260225120258-18275ca76b0c")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    modules = json.loads((SOURCE / "plugin-modules.json").read_bytes())
    sources = [(row["module"], row["version"]) for row in modules] + PINNED_TRANSPORT
    records = []
    with httpx.Client(timeout=120, trust_env=False, follow_redirects=True) as client:
        def fetch(item):
            module, version = item
            rows = []
            for suffix in ("info", "mod", "zip"):
                escaped = "".join("!" + char.lower() if char.isupper() else char for char in module)
                relative = escaped + "/@v/" + version + "." + suffix
                url = "https://proxy.golang.org/" + relative
                path = args.output / relative
                if path.exists():
                    data = path.read_bytes()
                    attempts = 0
                else:
                    # The observed BuildKit and official DB downloads returned EOF.
                    # Three bounded transport attempts preserve the same official URL.
                    for attempts in range(1, 4):
                        try:
                            response = client.get(url)
                            response.raise_for_status()
                            data = response.content
                            if suffix == "zip":
                                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                                    if archive.testzip() is not None:
                                        raise ValueError("Official source ZIP CRC mismatch")
                            break
                        except (httpx.TransportError, zipfile.BadZipFile):
                            if attempts == 3:
                                raise
                            time.sleep(1)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                rows.append({"url": url, "relative": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "transport_attempts": attempts})
            print("Prefetched official module " + module + "@" + version, flush=True)
            return rows
        for rows in ThreadPoolExecutor(max_workers=4).map(fetch, sources):
            records.extend(rows)
    (args.output.parent / "official-module-transport.json").write_text(json.dumps({"official_transport": records, "sumdb_verification_deferred_to_go": True, "artifact_approved": False}, indent=2) + "\n")


if __name__ == "__main__":
    main()
