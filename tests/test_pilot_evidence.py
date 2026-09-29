import csv
import json
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from scripts.pilot_evidence import prepare, summarize, telemetry


def test_extension_fixtures_keep_original_quality_gate_separate():
    root = Path(__file__).resolve().parents[1]
    baseline = json.loads((root / "eval/research_quality_cases.json").read_text(encoding="utf-8"))
    extension = json.loads((root / "eval/research_quality_extensions.json").read_text(encoding="utf-8"))
    assert any(case["id"] == "compare-1" for case in baseline)
    assert {case["id"] for case in baseline}.isdisjoint(case["id"] for case in extension)
    assert len(extension) == 4
    for case in extension:
        assert case["critical"] is True
        assert case["evidence"] and case["expected_facts"] and case["forbidden_claims"]
        assert "synthetic" in case["provenance"].lower()


def test_blind_pilot_requires_complete_reviews_before_unblinding(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cache").mkdir()
    source = tmp_path / "source.md"
    source.write_text("A frozen primary record.\n", encoding="utf-8")
    tasks = tmp_path / "tasks.json"
    tasks.write_text(json.dumps({"suite_id": "pilot-1", "tasks": [
        {"id": "q1", "question": "Which source is supported?", "success_criteria": "Cite the primary record.",
         "source_files": [str(source)]},
        {"id": "q2", "question": "What is uncertain?", "success_criteria": "State the limitation.",
         "source_files": [str(source)]},
    ]}), encoding="utf-8")
    submissions = []
    for task_id in ("q1", "q2"):
        for arm in ("manual", "agent"):
            answer = tmp_path / f"{task_id}-{arm}.md"
            answer.write_text(f"Answer for {task_id}\n", encoding="utf-8")
            submissions.append({"task_id": task_id, "arm": arm, "file": str(answer),
                                "elapsed_seconds": 120 if arm == "manual" else 45,
                                "model_cost_amount": 0 if arm == "manual" else 0.2,
                                "currency": "CNY"})
    submission_path = tmp_path / "submissions.json"
    submission_path.write_text(json.dumps({"submissions": submissions}), encoding="utf-8")
    review_dir = tmp_path / ".cache" / "review"
    private_dir = tmp_path / ".cache" / "private"
    prepared = prepare(Namespace(tasks=tasks, submissions=submission_path,
                                 review_dir=review_dir, private_dir=private_dir))
    assert prepared["answer_count"] == 4
    assert not (review_dir / "mapping.json").exists()
    assert len(list((review_dir / "answers").glob("*.md"))) == 4
    assert (review_dir / "sources" / "q1" / "source.md").is_file()

    review_path = review_dir / "review.csv"
    with review_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    summary_args = Namespace(review_dir=review_dir, private_dir=private_dir,
                             output=tmp_path / ".cache" / "summary.json")
    with pytest.raises(ValueError, match="reviewer_id"):
        summarize(summary_args)
    for row in rows:
        row.update({"reviewer_id": "r1", "usable_claims": "2", "factual_errors": "0",
                    "missing_requirements": "0", "edit_count": "1", "accepted": "1"})
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    summary = summarize(summary_args)
    assert summary["paired_mean_deltas"]["agent_minus_manual_elapsed_seconds"] == -75
    assert summary["paired_mean_deltas"]["agent_model_cost_total"] == 0.4
    assert summary["task_count"] == 2
    (review_dir / "answers" / f"{rows[0]['answer_code']}.md").write_text("Changed answer", encoding="utf-8")
    with pytest.raises(ValueError, match="answer changed"):
        summarize(summary_args)


def test_telemetry_reads_only_derived_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".cache").mkdir()
    database = tmp_path / "source.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE runs(id TEXT,user_id TEXT,status TEXT,created_at TEXT,updated_at TEXT,validation TEXT,report TEXT);
            CREATE TABLE events(id INTEGER PRIMARY KEY,run_id TEXT,type TEXT,data TEXT);
            CREATE TABLE counters(run_id TEXT,search_calls INTEGER,llm_calls INTEGER,prompt_tokens INTEGER,completion_tokens INTEGER);
            CREATE TABLE report_publications(source_run_id TEXT,status TEXT,review_reason TEXT,reviewed_at TEXT);
        """)
        connection.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", (
            "run-1", "workspace-a", "completed", "2026-09-01T00:00:00+00:00",
            "2026-09-01T00:01:00+00:00", json.dumps({"quality": "complete", "evidence_metrics": {"source_count": 3}}),
            "PRIVATE REPORT BODY"))
        connection.execute("INSERT INTO events(run_id,type,data) VALUES(?,?,?)", (
            "run-1", "reflection", '{"queries":["follow-up"]}'))
        connection.execute("INSERT INTO events(run_id,type,data) VALUES(?,?,?)", (
            "run-1", "node_error", '{"node":"web_scout"}'))
        connection.execute("INSERT INTO counters VALUES(?,?,?,?,?)", ("run-1", 2, 5, 100, 20))
        connection.execute("INSERT INTO report_publications VALUES(?,?,?,?)", (
            "run-1", "rejected", "Check the source", "2026-09-01T00:02:00+00:00"))
    output = tmp_path / ".cache" / "telemetry.json"
    assert telemetry(Namespace(sqlite=database, postgres_env=None, workspace_id="workspace-a",
                               run_id=None, output=output))["run_count"] == 1
    report = json.loads(output.read_text(encoding="utf-8"))["runs"][0]
    assert report["elapsed_seconds"] == 60
    assert report["reflection_count"] == 1
    assert report["failed_nodes"] == ["web_scout"]
    assert report["usage"]["prompt_tokens"] == 100
    assert report["publication"]["review_reason"] == "Check the source"
    assert "PRIVATE REPORT BODY" not in output.read_text(encoding="utf-8")
    assert telemetry(Namespace(sqlite=database, postgres_env=None, workspace_id="workspace-b",
                               run_id=None, output=output))["run_count"] == 0
