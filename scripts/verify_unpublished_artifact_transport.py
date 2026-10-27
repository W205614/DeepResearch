"""Read-only draft transport exercise. Never loads/tags an image or grants a CI publication gate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import fetch_approved_image as fetch  # noqa: E402
from scripts import grafana_image_disposition as grafana  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", choices=["grafana"], required=True)
    parser.add_argument("--verify-unpublished", action="store_true", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {"transport_verified": False, "security_gate_claim": False,
              "mode": "explicit_unpublished_transport_verification_only", "docker_operations": 0}
    try:
        binding = grafana.load_binding()
        transport = json.loads(args.manifest.read_bytes())
        publication = {}
        archive = fetch.download_archive(args.destination, binding, transport, artifact=args.artifact,
                                         allow_unpublished_verification=True, publication_receipt=publication)
        result.update(transport_verified=True, publication=publication, archive=str(archive),
                      facts=grafana.verify_archive(archive, binding))
    except Exception as error:
        result.update(transport_verified=False, error_type=type(error).__name__)
        if isinstance(error, fetch.DispositionError):
            result["error"] = str(error)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("transport_verified", "security_gate_claim", "mode", "error_type") if k in result}))
    raise SystemExit(0 if result["transport_verified"] else 1)


if __name__ == "__main__":
    main()
