"""Grounded recovery and report assembly; these helpers never approve a claim."""
import re

from ..domain.models import Claim, ReportDraft, ReportOutline, ReportSection, SourceFacts


def quoted_facts(extracted: SourceFacts, sources: list[dict]) -> list[dict]:
    """Accept only known sources and complete, contiguous original sentences."""
    originals = {source['id']: source for source in sources}
    result, seen = [], set()
    boundaries = '。！？!?\n'
    for fact in extracted.facts:
        source = originals.get(fact.source_id)
        quote = fact.quote.strip()
        if not source or not quote or (fact.source_id, quote) in seen:
            continue
        text = source['text']
        complete = any(
            (not text[:match.start()].rstrip(' \t\r')
             or text[:match.start()].rstrip(' \t\r')[-1] in boundaries)
            and (match.end() == len(text) or quote[-1] in boundaries or text[match.end()] == '\n')
            for match in re.finditer(re.escape(quote), text)
        )
        if not complete:
            continue
        seen.add((fact.source_id, quote))
        prefix = '图中显示：' if source.get('kind') == 'attachment' else '根据所提供资料，'
        result.append(Claim(text=prefix + quote, source_ids=[fact.source_id]).model_dump())
    return result


def assemble_report(topic: str, questions: list[str], claims: list[dict], outline: ReportOutline) -> ReportDraft:
    """Use each input fact once. Missing/unknown outline IDs cannot erase/add facts."""
    facts, keys = [], set()
    for value in claims:
        claim = Claim.model_validate(value)
        key = (re.sub(r'\s+', '', claim.text), tuple(sorted(claim.source_ids)))
        if key not in keys:
            keys.add(key)
            facts.append(claim)
    # Writer indices refer to the original analysis, before exact duplicate removal.
    lookup = {(re.sub(r'\s+', '', c.text), tuple(sorted(c.source_ids))): i for i, c in enumerate(facts)}
    indices = [lookup[(re.sub(r'\s+', '', c['text']), tuple(sorted(c['source_ids'])))] for c in claims]
    groups, used = {}, set()

    def collect(question_index, positions):
        for position in positions:
            if position not in used:
                used.add(position)
                groups.setdefault(question_index, []).append(facts[position])

    for section in outline.sections:
        index = section.question_index
        index = index if index is not None and index < len(questions) else None
        collect(index, [indices[i] for i in section.claim_indices if 0 <= i < len(indices)])
    collect(None, list(range(len(facts))))
    sections = []
    for index, selected in groups.items():
        heading = questions[index] if index is not None else '研究发现'
        for offset in range(0, len(selected), 12):
            sections.append(ReportSection(heading=heading[:150], claims=selected[offset:offset + 12]))
    # Five questions + an unassigned group: 40 facts need at most 8 chunks,
    # even when the model repeats or omits a section.
    return ReportDraft(title=topic[:160] or '研究报告', sections=sections)
