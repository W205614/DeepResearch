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
    deployment = evidence.get("deployment", {})
    python_services = deployment.get("python_services", [])
    if python_services and "uv_lock_normalized_sha256" in python_services[0]:
        lock = hashlib.sha256(Path("uv.lock").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if any(row["uv_lock_normalized_sha256"] != lock for row in python_services):
            raise ValueError("CI Python lock differs from the verified deployment images")
        frontend = hashlib.sha256(Path("frontend/package-lock.json").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if deployment["web"]["frontend_lock_normalized_sha256"] != frontend:
            raise ValueError("CI frontend lock differs from the verified Web build")
    java = evidence.get("java_os_security_followup")
    if java and hashlib.sha256(Path("business-backend/Dockerfile").read_bytes().replace(b"\r\n", b"\n")).hexdigest() != java["dockerfile_normalized_sha256"]:
        raise ValueError("CI Java Dockerfile differs from the verified deployment")
    evidence["ci_receipt"] = {
        "sha": os.environ["GITHUB_SHA"],
        "workflow_url": f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
        "job_status_before_receipt": os.environ["EVALUATION_JOB_STATUS"],
        "scope": "This Answer Quality job only; CI and Security checked separately for this same SHA",
    }
    reuse = Path(".cache/eval/quality-reuse.json")
    if reuse.exists():
        evidence["ci_receipt"]["quality_evidence"] = "reused"
        evidence["ci_receipt"]["reuse_verification"] = json.loads(reuse.read_bytes())
    else:
        evidence["ci_receipt"]["quality_evidence"] = "fresh"
    target = Path(".cache/eval/delivery.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
