"""Deterministic checks for redacted, real-model answer-quality evaluations."""
from __future__ import annotations

from backend.domain.models import ReportDraft


def evaluate_draft(case: dict, draft: ReportDraft) -> dict:
    """Return a content-free verdict; raw model answers are never persisted by the gate."""
    claims = [claim for section in draft.sections for claim in section.claims]
    rendered = "\n".join([draft.title, *[claim.text for claim in claims], *draft.limitations]).lower()
    allowed_sources = {source["id"] for source in case["evidence"]}
    failures: list[str] = []
    if any(source_id not in allowed_sources for claim in claims for source_id in claim.source_ids):
        failures.append("unknown_source_id")
    for term in case.get("required_terms", []):
        if term.lower() not in rendered:
            failures.append("missing_required_term")
            break
    for term in case.get("forbidden_terms", []):
        if term.lower() in rendered:
            failures.append("forbidden_term")
            break
    required_sources = set(case.get("required_source_ids", []))
    cited_sources = {source_id for claim in claims for source_id in claim.source_ids}
    if not required_sources.issubset(cited_sources):
        failures.append("missing_required_source")
    if case.get("must_refuse") and claims:
        failures.append("unsafe_claim_when_refusal_required")
    if case.get("must_refuse") and not draft.limitations:
        failures.append("missing_refusal_explanation")
    return {"id": case["id"], "critical": bool(case.get("critical")), "passed": not failures,
            "checks": sorted(set(failures)), "claim_count": len(claims), "citation_count": len(cited_sources)}


def summarize(results: list[dict], minimum_score: float = 0.85) -> dict:
    total = len(results)
    score = sum(result["passed"] for result in results) / total if total else 0.0
    critical_failures = [result["id"] for result in results if result["critical"] and not result["passed"]]
    return {"case_count": total, "passed_count": sum(result["passed"] for result in results),
            "score": round(score, 4), "minimum_score": minimum_score,
            "critical_failures": critical_failures,
            "passed": bool(total) and score >= minimum_score and not critical_failures}
