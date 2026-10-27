"""Program-owned typed verification and version compatibility boundary.

The graph orchestrates requests; this module owns immutable target text, canonical
verdict normalization, cache compatibility and persistent result compatibility.
"""
import json
import re
from dataclasses import dataclass
from typing import TypedDict, NotRequired, cast

from pydantic import ValidationError
from ..domain.models import Verification, TargetVerification


class VerificationTarget(TypedDict):
    id: str
    text: str


class VerificationClaim(TypedDict):
    index: int
    text: str
    source_ids: NotRequired[list[str]]
    targets: NotRequired[list[VerificationTarget]]


def validate_program_claims(claims: list[dict]) -> list[VerificationClaim]:
    if not isinstance(claims, list) or any(not isinstance(claim, dict) for claim in claims):
        raise ValueError('program claims must be a list of objects')
    indices = set()
    for claim in claims:
        index, text = claim.get('index'), claim.get('text')
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError('program claim index must be a nonnegative integer')
        if index in indices:
            raise ValueError('duplicate program claim index')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('program claim text must be nonempty')
        indices.add(index)
    return cast(list[VerificationClaim], claims)


def atomic_span_error(claim: str, atoms: list) -> str:
    """Atomic checks partition the actual claim, never a paraphrase of its source.

    Whitespace and punctuation between quoted spans are harmless. Every other
    character must be covered, in order, by nonoverlapping verbatim spans.
    """
    if not atoms:
        return "原子核验缺少待核验断言的原文片段"
    text = re.sub(r"\s+", "", claim)
    cursor, gaps = 0, []
    for atom in atoms:
        quote = re.sub(r"\s+", "", atom.text)
        start = text.find(quote, cursor) if quote else -1
        if start < 0:
            return "原子核验文本不是待核验断言中按顺序出现的连续原文"
        gaps.append(text[cursor:start])
        cursor = start + len(quote)
    gaps.append(text[cursor:])
    # This exact system attribution is presentation, not a factual proposition.
    # No other hedge, inference, negation or condition receives this exception.
    attribution = "根据所提供资料，"
    if claim.startswith(attribution) and gaps[0].startswith(attribution):
        gaps[0] = gaps[0][len(attribution):]
    # Keep signs, percentages and operators significant; only prose separators
    # may sit outside quoted spans.
    punctuation = "，。；：、,.;:!?！？“”‘’\"'（）()[]【】"
    if any(char not in punctuation for gap in gaps for char in gap):
        return "原子核验遗漏待核验断言中的内容"
    return ""


def normalize_verification(checks: Verification, claims: dict[int, str] | None = None) -> Verification:
    """Fail closed on unsupported semantics, unanchored checks or omitted text."""
    result = checks.model_copy(deep=True)
    for check in result.checks:
        if check.supported:
            text = (claims or {}).get(check.index)
            error = atomic_span_error(text, check.atomic_checks) if text is not None else "原子核验缺少对应的待核验断言"
            if error:
                check.verification_complete = False
                check.format_error = error
        if (not check.verification_complete or check.unsupported_parts or check.omitted_conditions
                or any(not atom.supported for atom in check.atomic_checks)):
            check.supported = False
    return result


def verification_targets(index: int, text: str) -> list[VerificationTarget]:
    # A whole claim is intentionally conservative. Splitting on commas can
    # detach negation, prerequisites or attribution from the proposition.
    return [{"id": f"{index}:0", "text": text}]


def bind_target_verification(verdict: TargetVerification, claims: list[dict]) -> Verification:
    """Bind complete, unique IDs to program-owned claim text; preserve semantic defects."""
    expected = {claim["index"]: claim for claim in validate_program_claims(claims)}
    unexpected = any(check.index not in expected for check in verdict.checks)
    checks = []
    for index, claim in expected.items():
        rows = [check for check in verdict.checks if check.index == index]
        error = ""
        if unexpected or len(rows) != 1:
            error = "核验结论编号缺失、重复或未知"
        row = rows[0] if len(rows) == 1 else None
        targets = verification_targets(index, claim["text"])
        ids = [atom.target_id for atom in row.atomic_checks] if row else []
        if row and (sorted(ids) != sorted(target["id"] for target in targets)
                    or any(atom.supported is None for atom in row.atomic_checks)):
            error = "核验目标编号缺失、重复、未知或缺少支持判断"
        atoms = []
        if row and not error:
            by_id = {atom.target_id: atom for atom in row.atomic_checks}
            atoms = [{"target_id": target["id"], "text": target["text"],
                      "supported": by_id[target["id"]].supported} for target in targets]
        checks.append({"index": index, "supported": bool(row and row.supported is not False and not error),
                       "atomic_checks": atoms, "unsupported_parts": row.unsupported_parts if row else [],
                       "omitted_conditions": row.omitted_conditions if row else [],
                       "reason": row.reason if row else "核验尚未完成",
                       "verification_complete": not bool(error), "format_error": error})
    return normalize_verification(Verification.model_validate({"checks": checks}),
                                  {index: claim["text"] for index, claim in expected.items()})


def supported_indices(checks: Verification, eligible: set[int], claims: dict[int, str] | None = None) -> set[int]:
    checks = normalize_verification(checks, claims)
    # Omitted or duplicated decisions fail closed.
    grouped = {}
    for check in checks.checks:
        grouped.setdefault(check.index, []).append(check.supported)
    return {i for i, values in grouped.items() if i in eligible and values == [True]}


@dataclass(frozen=True, slots=True)
class VerificationContract:
    contract_version: str = 'verification-v3-claim-span'
    wire_version: str = 'verification-v4-target-id'

    def matches_saved(self, saved: dict) -> bool:
        return (saved.get('contract_version') == self.contract_version
                and saved.get('wire_version') == self.wire_version)

    def matches_result(self, result: dict) -> bool:
        return (result.get('verification_contract') == self.contract_version
                and result.get('verification_wire') == self.wire_version)

    def load_saved(self, saved: dict | str, claims: dict[int, str], *, allow_partial=False) -> Verification | None:
        """Reuse only canonical decisions bound to these exact program-owned texts.

        A stale or corrupt cache is never evidence. The caller may continue its
        existing bounded verification path; this method performs no I/O or retry.
        """
        try:
            envelope = json.loads(saved) if isinstance(saved, str) else saved
            if not isinstance(envelope, dict) or not self.matches_saved(envelope):
                return None
            validate_program_claims([{'index': index, 'text': text} for index, text in claims.items()])
            if 'input' in envelope:
                inputs = validate_program_claims(envelope['input']['claims'])
                if {claim['index']:claim['text'] for claim in inputs} != claims:
                    return None
            value = normalize_verification(Verification.model_validate(envelope['verdict']), claims)
            if sorted(check.index for check in value.checks) != sorted(claims):
                return None
            for check in value.checks:
                if ([atom.target_id for atom in check.atomic_checks] != [f'{check.index}:0']
                        or check.atomic_checks[0].text != claims[check.index]):
                    check.verification_complete=False
                    check.supported=False
                    check.format_error='保存核验目标未严格绑定程序原文'
            if not allow_partial and not all(check.verification_complete for check in value.checks):
                return None
            return value
        except (KeyError, TypeError, ValueError, ValidationError):
            return None


CURRENT_VERIFICATION = VerificationContract()
VERIFICATION_CONTRACT = CURRENT_VERIFICATION.contract_version
VERIFICATION_WIRE = CURRENT_VERIFICATION.wire_version
