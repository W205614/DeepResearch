"""Tie coverage and evidence navigation to the final verified statements."""
from hashlib import sha256

from ..domain.models import QuestionCoverage


def text_id(text: str) -> str:
    return sha256(text.encode()).hexdigest()[:24]


def coverage_items(topic: str, questions: list[str], claims: list[dict],
                   coverage: QuestionCoverage) -> list[dict]:
    def item(text, status, claim_ids=(), gap=""):
        return {"id": text_id(text), "text": text, "status": status,
                "claim_ids": list(dict.fromkeys(claim_ids)),
                "gap": gap or ("尚缺可核验的证据" if status != "answered" else "")}

    result = {}
    for row in coverage.requirements:
        text = row.requirement.strip()
        if not text or text in result:
            continue
        claim_ids = [text_id(claims[i]["text"]) for i in row.claim_indices if 0 <= i < len(claims)]
        status = row.status if claim_ids else "insufficient"
        result[text] = item(text, status, claim_ids, row.gap if status != "answered" else "")
    if claims and coverage.original_request_answered and result and all(
        row["status"] == "answered" for row in result.values()
    ):
        return list(result.values())
    if not result:
        for index, text in enumerate(questions):
            if index not in coverage.answered_questions or not claims:
                result[text] = item(text, "insufficient")
            elif not coverage.requirements:
                result[text] = item(text, "answered")
    if not claims or not coverage.original_request_answered:
        if (not claims or all(row["status"] == "answered" for row in result.values())) and topic not in result:
            result[topic] = item(topic, "insufficient", gap="原始要求仍有遗漏，请明确需要补查的范围")
    elif not result:
        result[topic] = item(topic, "answered")
    return list(result.values())


def claim_links(claims: list[dict], comparisons: list[int]) -> list[dict]:
    return [{"id": text_id(claim["text"]), "text": claim["text"],
             "source_ids": list(dict.fromkeys(claim["source_ids"])),
             "comparison": index in comparisons and len(set(claim["source_ids"])) > 1}
            for index, claim in enumerate(claims)]
