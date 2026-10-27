"""Record this CI attempt without rebinding historical deployment evidence."""
import json
import hashlib
import os
from pathlib import Path

HISTORICAL = "docs/evidence/improvement-20261025/delivery.json"
SOURCE_FILES = ("backend/research/graph.py", "uv.lock", "frontend/package-lock.json",
                "business-backend/Dockerfile")
QUALITY_REPORTS = ("answer-quality.json", "research-quality.json", "research-stability.json",
                   "research-quality-extensions.json")
SUPPORTING_REPORTS = ("provider-budget.json", "ci-ledger-receipt.json", "ci-source-context.json")


def verify_historical_deployment(root):
    """Retain the original deployment checks, only for the existing reuse path."""
    source = root / HISTORICAL
    evidence = json.loads(source.read_bytes())
    if not isinstance(evidence, dict):
        raise ValueError("Historical deployment evidence is not an object")
    graph = (root / "backend/research/graph.py").read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(graph).hexdigest() != evidence["graph_normalized_sha256"]:
        raise ValueError("CI graph differs from the verified deployment source after LF normalization")
    deployment = evidence.get("deployment", {})
    python_services = deployment.get("python_services", [])
    if python_services and "uv_lock_normalized_sha256" in python_services[0]:
        lock = hashlib.sha256((root / "uv.lock").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if any(row["uv_lock_normalized_sha256"] != lock for row in python_services):
            raise ValueError("CI Python lock differs from the verified deployment images")
        frontend = hashlib.sha256((root / "frontend/package-lock.json").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if deployment["web"]["frontend_lock_normalized_sha256"] != frontend:
            raise ValueError("CI frontend lock differs from the verified Web build")
    java = evidence.get("java_os_security_followup")
    if java and hashlib.sha256((root / "business-backend/Dockerfile").read_bytes().replace(b"\r\n", b"\n")).hexdigest() != java["dockerfile_normalized_sha256"]:
        raise ValueError("CI Java Dockerfile differs from the verified deployment")
    return evidence


def file_record(root, relative, *, normalized=False, report=False):
    path = root / relative
    if not path.exists():
        return {"state": "missing" if normalized else "not-run"}
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        return {"state": "invalid-file"}
    try:
        raw = path.read_bytes()
    except OSError as error:
        return {"state": "unreadable", "error_type": type(error).__name__}
    row = {"state": "present", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if normalized:
        row["normalized_lf_sha256"] = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    if report:
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("Report is not an object")
            # Keep reported outcomes verbatim; this writer does not recalculate a quality/budget gate.
            row["reported"] = {key: value[key] for key in (
                "status", "precheck_status", "exit_code", "sealed", "passed", "summary", "run",
                "source_sha", "checkout_sha", "commit", "http_requests", "usage_missing_requests",
                "new_requests", "cumulative_requests", "supplier_http", "supplier_http_requests",
            ) if key in value}
        except ValueError:
            row["json_state"] = "invalid"
    return row


def write_receipt(root=Path("."), env=None):
    env = os.environ if env is None else env
    root = Path(root)
    output = root / ".cache/eval"
    reuse = output / "quality-reuse.json"
    mode = "reused" if reuse.exists() else "fresh"
    evidence = {
        "schema": "ci-evidence-receipt-v2", "kind": "ci_evidence_only",
        "status": env["EVALUATION_JOB_STATUS"], "receipt_status": "recorded",
        "deployment_performed": False, "deployment_verified_for_current_sha": False,
        "ci_receipt": {
            "sha": env["GITHUB_SHA"],
            "workflow_url": f"{env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}",
            "run_id": env["GITHUB_RUN_ID"], "run_attempt": env.get("GITHUB_RUN_ATTEMPT"),
            "job_status_before_receipt": env["EVALUATION_JOB_STATUS"], "quality_evidence": mode,
            "scope": "This Answer Quality attempt only; other workflow results and deployment are not asserted",
        },
        "current_source_fingerprints": {path: file_record(root, path, normalized=True) for path in SOURCE_FILES},
        "source_fingerprint_scope": "Listed current graph/locks/Java recipe only; the per-run full source inventory, if available, retains its own binding in ci-source-context.json",
        "reports": {},
        "report_scope": "Observed workspace files and their original outcomes; cumulative ledger history is not relabeled as new calls. Missing quality reports are not-run, never fabricated passes.",
    }
    names = set(QUALITY_REPORTS + SUPPORTING_REPORTS) | {
        p.name for p in output.glob("*.json") if p.name != "delivery.json"}
    for name in sorted(names):
        evidence["reports"][name] = file_record(root, ".cache/eval/" + name, report=True)
    evidence["ledger_files"] = {"provider-budget.sqlite3": file_record(root, ".cache/eval/provider-budget.sqlite3")}
    error = None
    if mode == "reused":
        try:
            historical = verify_historical_deployment(root)
            verification = json.loads(reuse.read_bytes())
            if (not isinstance(verification, dict) or verification.get("quality_evidence") != "reused"
                    or verification.get("target_sha") != env["GITHUB_SHA"]):
                raise ValueError("Quality reuse verification does not bind this CI SHA")
            evidence["ci_receipt"]["reuse_verification"] = verification
            evidence["historical_deployment_reference"] = {
                "path": HISTORICAL, "sha256": hashlib.sha256((root / HISTORICAL).read_bytes()).hexdigest(),
                "original_baseline_sha": historical.get("baseline_sha"), "original_status": historical.get("status"),
                "scope": "Historical evidence checked for reuse compatibility; not deployment evidence for the current CI SHA",
            }
        except (ValueError, OSError, KeyError) as failure:
            error = failure
            evidence["receipt_status"] = "invalid-reuse"
            evidence["reuse_error"] = {"type": type(failure).__name__, "message": str(failure)}
    target = output / "delivery.json"
    output.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    if error is not None:
        raise ValueError("Quality reuse receipt validation failed: " + str(error)) from error
    return evidence


def main():
    write_receipt()


if __name__ == "__main__":
    main()
