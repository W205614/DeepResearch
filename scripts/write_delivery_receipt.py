"""Bind committed evidence to the exact CI SHA without a self-referencing commit."""
import json
import hashlib
import os
from pathlib import Path


def main():
    source = Path("docs/evidence/improvement-20261025/delivery.json")
    evidence = json.loads(source.read_bytes())
    graph = Path("backend/research/graph.py").read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(graph).hexdigest() != evidence["graph_normalized_sha256"]:
        raise ValueError("CI graph differs from the verified deployment source after LF normalization")
    evidence["ci_receipt"] = {
        "sha": os.environ["GITHUB_SHA"],
        "workflow_url": f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        "job_status_before_receipt": os.environ["EVALUATION_JOB_STATUS"],
        "scope": "This Answer Quality job only; CI and Security checked separately for this same SHA",
    }
    target = Path(".cache/eval/delivery.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
