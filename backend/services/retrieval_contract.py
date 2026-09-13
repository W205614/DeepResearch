"""Validate adapter/cache data; provenance always comes from authorized SQL rows."""
import math

from ..infrastructure.providers import ServiceError


class RetrievalResults(list):
    def __init__(self, rows=(), *, reasons=()):
        super().__init__(rows)
        self.reasons = sorted(set(reasons))


def valid_chunk(row, user):
    return (isinstance(row, dict) and row.get('user_id') == user
            and all(isinstance(row.get(key), str) and row[key].strip()
                    for key in ('id', 'document_id', 'text', 'locator', 'title'))
            and len(row['text']) <= 12000)


def score(value):
    # Numeric JSON strings are a safe adapter conversion; booleans/NaN are not scores.
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('invalid score')
    value = float(value)
    if not math.isfinite(value) or not -1 <= value <= 1:
        raise ValueError('invalid cosine score')
    return value


def normalize_hits(hits, chunks_by_id, maximum):
    if not isinstance(hits, list):
        raise ServiceError('向量检索返回格式无效，已尝试关键词检索')
    output, rejected = {}, len(hits) > maximum
    for hit in hits[:maximum]:
        try:
            if not isinstance(hit, dict) or not isinstance(hit.get('id'), str):
                raise ValueError('missing id')
            key = hit['id']
            if key not in chunks_by_id:
                raise ValueError('unauthorized or stale id')
            value = score(hit.get('score'))
            output[key] = max(output.get(key, -1), value)
        except (TypeError, ValueError):
            rejected = True
    return output, rejected
