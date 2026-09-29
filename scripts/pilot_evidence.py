"""Prepare blinded pilot reviews and summarize local task evidence.

Keep private mapping, source answers, and review results in ignored local paths.
This tool never calls a model or transmits data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


REVIEW_FIELDS = (
    "answer_code", "task_id", "question", "success_criteria", "reviewer_id",
    "usable_claims", "factual_errors", "missing_requirements", "edit_count",
    "accepted", "rejection_reason",
)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def worktree_clean() -> bool | None:
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)
    return not result.stdout.strip() if result.returncode == 0 else None


def require_local_dir(path: Path) -> None:
    # Pilot material can contain unpublished reports. Keep it under ignored .cache.
    resolved = path.resolve()
    cache = (Path.cwd() / ".cache").resolve()
    if resolved != cache and cache not in resolved.parents:
        raise ValueError(f"Pilot output must stay under {cache}")


def prepare(args: argparse.Namespace) -> dict:
    review_dir, private_dir = args.review_dir.resolve(), args.private_dir.resolve()
    require_local_dir(review_dir)
    require_local_dir(private_dir)
    if review_dir == private_dir or review_dir in private_dir.parents or private_dir in review_dir.parents:
        raise ValueError("Review and private directories must be separate")
    if review_dir.exists() or private_dir.exists():
        raise FileExistsError("Use new review/private directories for each pilot round")

    manifest = load_json(args.tasks)
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Add real, consented research tasks to a private task manifest before preparing a pilot")
    task_by_id = {}
    for task in tasks:
        if (not isinstance(task, dict) or not all(isinstance(task.get(key), str) and task[key].strip()
                                               for key in ("id", "question", "success_criteria"))
                or not re.fullmatch(r"[A-Za-z0-9_-]+", task["id"])):
            raise ValueError("Each task needs a safe id, question, and success_criteria")
        if task["id"] in task_by_id:
            raise ValueError(f"Duplicate task id: {task['id']}")
        references = task.get("source_files")
        if not isinstance(references, list) or not references:
            raise ValueError(f"Task {task['id']} needs frozen source_files for independent review")
        names = set()
        for reference in references:
            path = Path(reference)
            if not path.is_file() or path.suffix.lower() not in {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
                raise ValueError(f"Task {task['id']} has a missing or unsupported source file: {reference}")
            if path.name in names:
                raise ValueError(f"Task {task['id']} has duplicate source filenames: {path.name}")
            names.add(path.name)
        task_by_id[task["id"]] = task

    submissions = load_json(args.submissions).get("submissions")
    if not isinstance(submissions, list):
        raise ValueError("Submissions must be a list")
    by_pair = {}
    for row in submissions:
        if not isinstance(row, dict) or row.get("task_id") not in task_by_id or row.get("arm") not in {"manual", "agent"}:
            raise ValueError("Each submission needs a known task_id and arm=manual|agent")
        key = (row["task_id"], row["arm"])
        if key in by_pair:
            raise ValueError(f"Duplicate submission: {key}")
        if (not isinstance(row.get("elapsed_seconds"), (int, float)) or isinstance(row["elapsed_seconds"], bool)
                or not math.isfinite(row["elapsed_seconds"]) or row["elapsed_seconds"] <= 0):
            raise ValueError(f"Elapsed seconds must be positive for {key}")
        cost = row.get("model_cost_amount")
        if cost is not None and (not isinstance(cost, (int, float)) or isinstance(cost, bool)
                                 or not math.isfinite(cost) or cost < 0 or not row.get("currency")):
            raise ValueError(f"Cost needs a nonnegative amount and currency for {key}; use null if unknown")
        source = Path(row.get("file", ""))
        if not source.is_file() or not source.read_text(encoding="utf-8").strip():
            raise ValueError(f"Nonempty UTF-8 answer file required for {key}: {source}")
        by_pair[key] = {**row, "file": str(source.resolve())}
    expected = {(task_id, arm) for task_id in task_by_id for arm in ("manual", "agent")}
    if set(by_pair) != expected:
        raise ValueError(f"Missing submissions: {sorted(expected - set(by_pair))}")

    review_dir.mkdir(parents=True)
    (review_dir / "answers").mkdir()
    private_dir.mkdir(parents=True)
    source_hashes = {}
    for task_id, task in task_by_id.items():
        source_dir = review_dir / "sources" / task_id
        source_dir.mkdir(parents=True)
        source_hashes[task_id] = []
        for reference in task["source_files"]:
            shutil.copyfile(reference, source_dir / Path(reference).name)
            copied = source_dir / Path(reference).name
            source_hashes[task_id].append({"name": copied.name,
                                           "sha256": sha256_file(copied)})
    mapping = []
    review_rows = []
    for task_id, task in task_by_id.items():
        pairs = [by_pair[(task_id, arm)] for arm in ("manual", "agent")]
        if secrets.randbelow(2):
            pairs.reverse()
        for submission in pairs:
            code = secrets.token_hex(5)
            shutil.copyfile(submission["file"], review_dir / "answers" / f"{code}.md")
            mapping.append({"answer_code": code, "task_id": task_id, "arm": submission["arm"],
                            "question": task["question"], "success_criteria": task["success_criteria"],
                            "answer_sha256": sha256_file(review_dir / "answers" / f"{code}.md"),
                            "elapsed_seconds": submission["elapsed_seconds"],
                            "model_cost_amount": submission.get("model_cost_amount"),
                            "currency": submission.get("currency"), "run_id": submission.get("run_id")})
            review_rows.append({"answer_code": code, "task_id": task_id, "question": task["question"],
                                "success_criteria": task["success_criteria"]})
    review_path = review_dir / "review.csv"
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(review_rows)
    write_json(private_dir / "mapping.json", {
        "suite_id": manifest.get("suite_id", "unnamed"),
        "task_manifest_sha256": sha256_file(args.tasks),
        "source_commit": git_commit(), "source_worktree_clean": worktree_clean(),
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "assignments": mapping, "source_hashes": source_hashes,
    })
    return {"task_count": len(tasks), "answer_count": len(mapping), "review_dir": str(review_dir),
            "private_dir": str(private_dir)}


def nonnegative_int(row: dict, field: str) -> int:
    raw = row.get(field, "")
    if not raw or not raw.isdigit():
        raise ValueError(f"Review {row.get('answer_code', '')}: {field} must be a nonnegative integer")
    return int(raw)


def summarize(args: argparse.Namespace) -> dict:
    require_local_dir(args.review_dir)
    require_local_dir(args.private_dir)
    require_local_dir(args.output.parent)
    mapping = load_json(args.private_dir / "mapping.json")
    assignments = {row["answer_code"]: row for row in mapping["assignments"]}
    for code, assignment in assignments.items():
        answer = args.review_dir / "answers" / f"{code}.md"
        if sha256_file(answer) != assignment["answer_sha256"]:
            raise ValueError(f"Blinded answer changed after preparation: {code}")
    for task_id, sources in mapping["source_hashes"].items():
        for source in sources:
            path = args.review_dir / "sources" / task_id / source["name"]
            if sha256_file(path) != source["sha256"]:
                raise ValueError(f"Frozen source changed after preparation: {task_id}/{source['name']}")
    with (args.review_dir / "review.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or {row["answer_code"] for row in rows} != set(assignments):
        raise ValueError("Every blinded answer must have a completed review")
    seen = set()
    ratings = {}
    reviewers = set()
    for row in rows:
        code, reviewer = row["answer_code"], row.get("reviewer_id", "").strip()
        if (code not in assignments or row["task_id"] != assignments[code]["task_id"] or not reviewer
                or row["question"] != assignments[code]["question"]
                or row["success_criteria"] != assignments[code]["success_criteria"]):
            raise ValueError("Unknown answer, changed task criteria, or missing reviewer_id")
        if (code, reviewer) in seen:
            raise ValueError(f"Duplicate reviewer/answer pair: {reviewer}/{code}")
        seen.add((code, reviewer))
        reviewers.add(reviewer)
        accepted = row.get("accepted", "").strip()
        if accepted not in {"0", "1"}:
            raise ValueError("accepted must be 0 or 1")
        score = {key: nonnegative_int(row, key) for key in
                 ("usable_claims", "factual_errors", "missing_requirements", "edit_count")}
        score["accepted"] = int(accepted)
        if not score["accepted"] and not row.get("rejection_reason", "").strip():
            raise ValueError("Rejected answers need a reason")
        score["rejection_reason"] = row.get("rejection_reason", "").strip()
        ratings.setdefault(code, []).append(score)
    if any((code, reviewer) not in seen for code in assignments for reviewer in reviewers):
        raise ValueError("Every reviewer must score every answer before unblinding")

    by_task = {}
    for code, assignment in assignments.items():
        scores = ratings[code]
        count = len(scores)
        averaged = {key: sum(score[key] for score in scores) / count for key in
                    ("usable_claims", "factual_errors", "missing_requirements", "edit_count", "accepted")}
        averaged.update({"elapsed_seconds": assignment["elapsed_seconds"],
                         "model_cost_amount": assignment["model_cost_amount"],
                         "currency": assignment.get("currency"), "run_id": assignment.get("run_id"),
                         "rejection_reasons": [score["rejection_reason"] for score in scores if score["rejection_reason"]]})
        by_task.setdefault(assignment["task_id"], {})[assignment["arm"]] = averaged
    if any(set(arms) != {"manual", "agent"} for arms in by_task.values()):
        raise ValueError("Each task needs both methods")
    paired = {}
    for metric in ("elapsed_seconds", "usable_claims", "factual_errors", "missing_requirements", "edit_count", "accepted"):
        paired[f"agent_minus_manual_{metric}"] = round(sum(
            arms["agent"][metric] - arms["manual"][metric] for arms in by_task.values()) / len(by_task), 3)
    costs = [arms["agent"]["model_cost_amount"] for arms in by_task.values()]
    currencies = {arms["agent"]["currency"] for arms in by_task.values() if arms["agent"]["model_cost_amount"] is not None}
    paired["agent_model_cost_total"] = round(sum(costs), 4) if all(cost is not None for cost in costs) and len(currencies) == 1 else None
    paired["cost_currency"] = next(iter(currencies)) if paired["agent_model_cost_total"] is not None else None
    result = {"suite_id": mapping["suite_id"], "task_manifest_sha256": mapping["task_manifest_sha256"],
              "source_hashes": mapping["source_hashes"],
              "source_commit": mapping["source_commit"],
              "source_worktree_clean": mapping["source_worktree_clean"],
              "reviewers": len(reviewers), "task_count": len(by_task),
              "review_sheet_sha256": sha256_file(args.review_dir / "review.csv"),
              "paired_mean_deltas": paired, "task_results": by_task,
              "evidence_scope": "Local paired pilot only; no population or production claim."}
    write_json(args.output, result)
    return {"output": str(args.output), "task_count": len(by_task), "reviewers": len(reviewers),
            "paired_mean_deltas": paired}


def db_rows(connection, sql: str, params: tuple = ()) -> list[dict]:
    if isinstance(connection, sqlite3.Connection):
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
    return list(connection.execute(sql.replace("?", "%s"), params).fetchall())


def connect_read_only(args: argparse.Namespace):
    if args.sqlite:
        source = args.sqlite.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        connection = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    dsn = os.environ.get(args.postgres_env, "") if args.postgres_env else ""
    if not dsn:
        raise ValueError("Provide --sqlite or set the selected PostgreSQL DSN environment variable")
    import psycopg
    from psycopg.rows import dict_row
    connection = psycopg.connect(dsn, row_factory=dict_row)
    connection.read_only = True
    return connection


def parse_json(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def elapsed(start: str, end: str) -> float | None:
    try:
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 2)
    except (TypeError, ValueError):
        return None


def telemetry(args: argparse.Namespace) -> dict:
    require_local_dir(args.output.parent)
    with closing(connect_read_only(args)) as connection:
        params = (args.workspace_id,)
        where = "user_id=?"
        if args.run_id:
            where += " AND id=?"
            params += (args.run_id,)
        runs = db_rows(connection, f"SELECT id,status,created_at,updated_at,validation FROM runs WHERE {where} ORDER BY created_at DESC LIMIT 100", params)
        has_publications = bool(db_rows(connection, "SELECT name FROM sqlite_master WHERE type='table' AND name='report_publications'")) if args.sqlite else True
        result = []
        for run in runs:
            run_id = run["id"]
            events = db_rows(connection, "SELECT type,data FROM events WHERE run_id=? AND type IN ('reflection','node_error','node_end') ORDER BY id", (run_id,))
            counters = db_rows(connection, "SELECT search_calls,llm_calls,prompt_tokens,completion_tokens FROM counters WHERE run_id=?", (run_id,))
            reviews = db_rows(connection, "SELECT status,review_reason,reviewed_at FROM report_publications WHERE source_run_id=?", (run_id,)) if has_publications else []
            validation = parse_json(run["validation"])
            reflections = [parse_json(event["data"]) for event in events if event["type"] == "reflection"]
            errors = [parse_json(event["data"]).get("node") for event in events if event["type"] == "node_error"]
            durations = [parse_json(event["data"]).get("duration_ms") for event in events if event["type"] == "node_end"]
            result.append({"run_id": run_id, "status": run["status"],
                           "elapsed_seconds": elapsed(run["created_at"], run["updated_at"]),
                           "quality": validation.get("quality"),
                           "source_count": (validation.get("evidence_metrics") or {}).get("source_count"),
                           "reflection_count": sum(bool(item.get("queries")) for item in reflections),
                           "reflection_stop_reasons": [item["stop_reason"] for item in reflections if item.get("stop_reason")],
                           "failed_nodes": [node for node in errors if node],
                           "node_duration_ms_total": sum(value for value in durations if isinstance(value, (int, float))),
                           "usage": counters[0] if counters else None,
                           "publication": reviews[0] if reviews else None,
                           "model_cost_amount": None})
    report = {"workspace_id": args.workspace_id, "source": "read-only database snapshot",
              "captured_at": datetime.now(timezone.utc).isoformat(),
              "cost_note": "Token counts are not billed monetary cost; supply invoice cost separately.",
              "runs": result}
    write_json(args.output, report)
    return {"output": str(args.output), "run_count": len(result)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    first = subcommands.add_parser("prepare", help="Create blinded answer files and private assignment mapping")
    first.add_argument("--tasks", type=Path, required=True)
    first.add_argument("--submissions", type=Path, required=True)
    first.add_argument("--review-dir", type=Path, required=True)
    first.add_argument("--private-dir", type=Path, required=True)
    second = subcommands.add_parser("summarize", help="Unblind only after all reviews are complete")
    second.add_argument("--review-dir", type=Path, required=True)
    second.add_argument("--private-dir", type=Path, required=True)
    second.add_argument("--output", type=Path, required=True)
    third = subcommands.add_parser("telemetry", help="Read existing run/event/counter/review data without prompts")
    source = third.add_mutually_exclusive_group(required=True)
    source.add_argument("--sqlite", type=Path)
    source.add_argument("--postgres-env", metavar="ENV_VAR")
    third.add_argument("--workspace-id", required=True)
    third.add_argument("--run-id")
    third.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    action = {"prepare": prepare, "summarize": summarize, "telemetry": telemetry}[args.command]
    print(json.dumps(action(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
