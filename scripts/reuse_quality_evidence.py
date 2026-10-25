"""One approved Java-OS-only commit: verify existing paid evidence, never synthesize a fresh run."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import Settings  # noqa: E402
from backend.quality_gate import summarize  # noqa: E402
from scripts.evaluate_research import summarize_repetitions  # noqa: E402

SOURCE = "c764c907df7c6cc5f5cb781871dc0cf8c0fc50bb"
REPO = "W205614/DeepResearch"
RUN = 40183294157
ARTIFACT = 12594928509
DIGEST = "a1e5ee721e721d0ee50fb9765581bd3dbe647769e3735091bbe84f08658c345f"
FILES = {"answer-quality.json", "research-quality.json", "research-stability.json",
         "research-quality-extensions.json", "provider-budget.json", "delivery.json"}
PROTECTED = ["Dockerfile", "pyproject.toml", "uv.lock", "scripts/evaluate_answers.py",
             "scripts/evaluate_research.py", "scripts/bounded_evaluation.py",
             "scripts/prepare_python_runtime.py", "eval/answer_quality_cases.json",
             "eval/research_quality_cases.json", "eval/research_quality_extensions.json"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def check_source(run, artifact):
    if (run.get("repository", {}).get("full_name") != REPO or run.get("head_sha") != SOURCE
            or run.get("id") != RUN or run.get("workflow_id") != 354007725
            or run.get("path") != ".github/workflows/answer-quality.yml"
            or run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise ValueError("Paid evidence source is not the exact approved successful workflow")
    if (artifact.get("id") != ARTIFACT or artifact.get("expired") is not False
            or artifact.get("workflow_run", {}).get("head_sha") != SOURCE
            or artifact.get("workflow_run", {}).get("id") != RUN
            or artifact.get("name") != "answer-quality-evidence-" + SOURCE
            or artifact.get("digest") != "sha256:" + DIGEST):
        raise ValueError("Artifact identity or digest differs")


def unpack(blob):
    if sha(blob) != DIGEST:
        raise ValueError("Artifact checksum differs")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        if set(archive.namelist()) != FILES or sum(x.file_size for x in archive.infolist()) > 5_000_000:
            raise ValueError("Unexpected artifact entries or size")
        return {name: archive.read(name) for name in FILES}


def check_fingerprints(expected, current):
    if expected != current:
        raise ValueError("Protected implementation, dependencies or evaluation input differs")


def check_diff(changed, allowed):
    if not changed or not set(changed) <= set(allowed) or "business-backend/Dockerfile" not in changed:
        raise ValueError("Change lies outside the approved Java-OS-only scope")


def choose_mode(manifest, parent):
    return "reuse" if manifest and parent == SOURCE else "fresh"


def check_inputs(manifest):
    names = PROTECTED + git("ls-tree", "-r", "--name-only", SOURCE, "backend").decode().splitlines()
    before = {name: sha(git("show", SOURCE + ":" + name).replace(b"\r\n", b"\n")) for name in names}
    current = {name: sha((ROOT / name).read_bytes().replace(b"\r\n", b"\n")) for name in names}
    check_fingerprints(before, current)
    changed = git("diff", "--name-only", SOURCE, "HEAD").decode().splitlines()
    check_diff(changed, manifest["allowed_diff_paths"])
    if sha((ROOT / "business-backend/Dockerfile").read_bytes().replace(b"\r\n", b"\n")) != manifest["java_dockerfile_sha256"]:
        raise ValueError("Java Dockerfile is not the approved exact patch")
    # The four original invocations and their original flags must still exist.
    original = git("show", SOURCE + ":.github/workflows/answer-quality.yml").decode()
    current_workflow = (ROOT / ".github/workflows/answer-quality.yml").read_text(encoding="utf-8")
    commands = [line.strip() for line in original.splitlines() if "- run: uv run python scripts/bounded_evaluation.py" in line]
    if len(commands) != 4 or any(command not in current_workflow for command in commands):
        raise ValueError("Existing fresh evaluation invocations changed")
    return current


def check_reports(raw, settings):
    reports = {name: json.loads(data) for name, data in raw.items()}
    answers = reports["answer-quality.json"]
    if answers["summary"] != summarize(answers["results"]) or not answers["summary"]["passed"]:
        raise ValueError("Original answer summary fails unchanged thresholds")
    if answers["summary"]["case_count"] != 10:
        raise ValueError("Original answer cohort differs")
    backend = hashlib.sha256()
    for path in sorted((ROOT / "backend").rglob("*.py")):
        backend.update(path.relative_to(ROOT).as_posix().encode() + b"\0"
                       + path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    expected_config = {"model": settings.llm_model_id,
                       "model_base_url_sha256": sha(settings.llm_base_url.encode()),
                       "extra_body_sha256": sha(settings.llm_extra_body.encode())}
    ledger = reports["provider-budget.json"]
    contexts = ledger.get("execution_contexts", [])
    if (ledger["http_requests"] != 414 or ledger["usage_missing_requests"] != 0 or len(contexts) != 4
            or {row["label"] for row in contexts} != {"answers", "research", "stability", "extensions"}):
        raise ValueError("Original paid ledger is incomplete")
    targets = {"answers": "scripts/evaluate_answers.py", "research": "scripts/evaluate_research.py",
               "stability": "scripts/evaluate_research.py", "extensions": "scripts/evaluate_research.py"}
    for context in contexts:
        if (any(context.get(k) != v for k, v in expected_config.items())
                or context["checkout_sha"] != SOURCE or context["working_tree_dirty"] is not False
                or context["backend_normalized_source_sha256"] != backend.hexdigest()
                or context["target_sha256"] != sha((ROOT / targets[context["label"]]).read_bytes().replace(b"\r\n", b"\n"))):
            raise ValueError("Effective model configuration or source fingerprint differs")
    for name, casefile, selected, repetitions, require_all, count in [
        ("research-quality.json", "eval/research_quality_cases.json", None, 1, False, 30),
        ("research-stability.json", "eval/research_quality_cases.json", {"conflict-1", "compare-3", "single-5"}, 3, True, 9),
        ("research-quality-extensions.json", "eval/research_quality_extensions.json", None, 1, False, 4),
    ]:
        raw_cases = (ROOT / casefile).read_bytes().replace(b"\r\n", b"\n")
        cases = json.loads(raw_cases)
        if selected:
            cases = [case for case in cases if case["id"] in selected]
        report = reports[name]
        computed = summarize_repetitions(cases, report["results"], repetitions, require_all)
        if (report["summary"] != computed or not computed["passed"] or computed["case_count"] != count
                or report["fixture_sha256"] != sha(raw_cases) or report["commit"] != SOURCE
                or report["backend_source_sha256"] != backend.hexdigest()
                or report["model"] != settings.llm_model_id):
            raise ValueError("Original research inputs, summaries or thresholds differ")
    receipt = reports["delivery.json"]["ci_receipt"]
    if (receipt["sha"] != SOURCE or receipt["job_status_before_receipt"] != "success"
            or receipt["workflow_url"] != f"https://github.com/{REPO}/actions/runs/{RUN}"):
        raise ValueError("Original receipt is not successful paid source evidence")
    return {name: reports[name]["summary"] for name in FILES if "summary" in reports[name]}


def main(args):
    manifest_path = ROOT / "eval/quality-reuse-once.json"
    manifest = json.loads(manifest_path.read_bytes()) if manifest_path.exists() else None
    parent = git("show", "-s", "--format=%P", "HEAD").decode().strip()
    applicable = choose_mode(manifest, parent) == "reuse"
    if args.probe:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("mode=" + ("reuse" if applicable else "fresh") + "\n")
        return
    if not applicable or os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise ValueError("Not the one approved direct-child commit")
    if (manifest["source_sha"] != SOURCE or manifest["source_run_id"] != RUN
            or manifest["artifact_id"] != ARTIFACT or manifest["artifact_sha256"] != DIGEST
            or manifest["approved_utc_date"] != datetime.now(timezone.utc).date().isoformat()):
        raise ValueError("One-time authorization identity or date differs")
    fingerprints = check_inputs(manifest)
    headers = {"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"], "Accept": "application/vnd.github+json"}
    with httpx.Client(headers=headers, follow_redirects=True, trust_env=False, timeout=60) as client:
        def fetch(path):
            response = client.get(f"https://api.github.com/repos/{REPO}/" + path)
            response.raise_for_status()
            return response
        run = fetch(f"actions/runs/{RUN}").json()
        artifact = fetch(f"actions/artifacts/{ARTIFACT}").json()
        check_source(run, artifact)
        if artifact.get("size_in_bytes", 5_000_001) > 5_000_000:
            raise ValueError("Artifact download exceeds approved size")
        chunks, size = [], 0
        with client.stream("GET", f"https://api.github.com/repos/{REPO}/actions/artifacts/{ARTIFACT}/zip") as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > 5_000_000:
                    raise ValueError("Artifact download exceeds approved size")
                chunks.append(chunk)
        blob = b"".join(chunks)
    raw = unpack(blob)
    summaries = check_reports(raw, Settings())
    output = ROOT / ".cache/eval"
    source = output / "reused-source"
    source.mkdir(parents=True, exist_ok=False)
    for name, data in raw.items():
        (source / name).write_bytes(data)
    record = {"quality_evidence": "reused", "source_sha": SOURCE, "source_run_id": RUN,
              "source_workflow_url": f"https://github.com/{REPO}/actions/runs/{RUN}",
              "source_artifact_id": ARTIFACT, "source_artifact_sha256": DIGEST,
              "target_sha": os.environ["GITHUB_SHA"], "actual_target_model_http_requests": 0,
              "actual_target_prompt_tokens": 0, "actual_target_completion_tokens": 0,
              "protected_fingerprints": fingerprints, "changed_paths_checked": git("diff", "--name-only", SOURCE, "HEAD").decode().splitlines(),
              "unchanged_source_summaries": summaries,
              "limitations": "Original paid evidence retained byte-for-byte; no new generation, grading or supplier-availability claim. Future commits not directly parented by this source take the original fresh gate."}
    (output / "quality-reuse.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    (output / "provider-budget.json").write_text(json.dumps({"quality_evidence": "reused", "http_requests": 0,
        "actual_prompt_tokens": 0, "actual_completion_tokens": 0, "usage_missing_requests": 0,
        "charged_input_tokens": 0, "charged_output_tokens": 0, "requests": [],
        "source_sha": SOURCE, "source_provider_budget": "reused-source/provider-budget.json"}, indent=2), encoding="utf-8")
    print("Approved immutable quality evidence verified; target model requests: 0")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    main(parser.parse_args())
