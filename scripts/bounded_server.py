"""Explicit isolated real-provider acceptance entrypoint with one shared Linux ledger."""
import json
import os
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.bounded_evaluation import Budget  # noqa: E402


def main():
    if (os.environ.get("OPTIMIZATION_LIVE_ACCEPTANCE") != "1"
            or not os.environ.get("DATABASE_URL", "").endswith("/dr_opt_live")
            or os.environ.get("OIDC_ISSUER") != "http://localhost:18481/realms/deepresearch"):
        raise SystemExit("Explicit isolated acceptance database and identity are required")
    role = sys.argv[1] if len(sys.argv) == 2 else ""
    if role not in {"agent", "worker", "document-worker"}:
        raise SystemExit("One isolated runtime role is required")
    policy = json.loads(Path("/opt-policy/live-policy.json").read_text())
    # Frozen local allowance; CI has its own separately authorized series.
    if policy != {"model_http": 23, "llm_http": 23, "embedding_http": 20,
                  "reranker_http": 0, "search_http": 0, "total_http": 43,
                  "input_tokens": 600000, "output_tokens": 117417, "reserved_tokens": 800000}:
        raise SystemExit("Frozen isolated policy mismatch")
    budget = Budget("/opt-live/ledger.sqlite3", policy, "frozen-live-" + role)
    budget.install()
    try:
        if role == "agent":
            import uvicorn
            uvicorn.run("backend.api.app:create_app", factory=True, host="0.0.0.0", port=8000, access_log=False)
        else:
            worker = "DocumentWorkerSettings" if role == "document-worker" else "WorkerSettings"
            sys.argv = ["arq", "backend.worker." + worker]
            runpy.run_module("arq", run_name="__main__")
    finally:
        Path("/opt-live/ledger-" + role + ".json").write_text(json.dumps(budget.report(), indent=2))


if __name__ == "__main__":
    main()
