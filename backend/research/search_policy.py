"""Deterministic search scheduling, independent of model and storage adapters."""
from collections.abc import Iterable

from ..core.security import canonical_url
from ..infrastructure.providers import ServiceError


def normalize_queries(values: Iterable[str], *, searched: Iterable[str] = ()) -> list[str]:
    """Keep the first spelling, collapsing whitespace and case-only duplicates.

    Do not remove punctuation or rewrite terms: those can change search intent.
    """
    seen = {" ".join(value.split()).casefold() for value in searched}
    result = []
    for value in values:
        query = " ".join(value.split())
        key = query.casefold()
        if query and key not in seen:
            result.append(query)
            seen.add(key)
    return result


def select_web_candidates(rows_by_query: list[list[dict]], limit: int) -> dict[str, dict]:
    """Give each query one unique valid URL per turn, preserving rank order.

    Invalid and duplicate rows do not spend a query's turn. URL normalization
    is only selection; the web reader must still enforce DNS/redirect safety.
    """
    selected = {}
    pending = [iter(rows) for rows in rows_by_query]
    while pending and len(selected) < limit:
        remaining = []
        for rows in pending:
            for row in rows:
                try:
                    url = canonical_url(row.get("url", ""))
                except (ServiceError, ValueError):
                    continue
                if url in selected:
                    continue
                selected[url] = row
                remaining.append(rows)
                break
            if len(selected) >= limit:
                break
        pending = remaining
    return selected
