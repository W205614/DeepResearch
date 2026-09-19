"""Bounded HTTP reranker adapter with strict response validation."""
from __future__ import annotations

import asyncio
import math
import time

import httpx

from ..core.metrics import RERANKER_CALLS, RERANKER_SECONDS
from ..core.reliability import ServiceError


class RerankerClient:
    def __init__(self, settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=settings.reranker_timeout_seconds, trust_env=False)
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.reranker_enabled)

    @property
    def contract_key(self) -> str:
        return (f"rerank-v1:{self.settings.reranker_model}:{self.settings.reranker_candidate_limit}"
                if self.enabled else "fusion-v1")

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def rerank(self, query: str, rows: list[dict], top_n: int) -> list[tuple[int, float]]:
        if not self.enabled:
            return []
        if not self.settings.reranker_url or not self.settings.reranker_model:
            RERANKER_CALLS.labels(outcome="error").inc()
            raise ServiceError("重排服务配置不完整，已回退融合排序", code="reranker_unavailable", retryable=False)
        if not self.settings.demo_mode and not self.settings.allow_internal_model_processing:
            RERANKER_CALLS.labels(outcome="policy_blocked").inc()
            raise ServiceError("内部资料未获准发送到重排服务", code="reranker_policy_blocked", retryable=False)
        headers = {"Content-Type": "application/json"}
        secret = self.settings.reranker_api_key.get_secret_value()
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        payload = {
            "model": self.settings.reranker_model,
            "query": query,
            "documents": [{"id": row["id"], "text": row["text"]} for row in rows],
            "top_n": top_n,
        }
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.reranker_timeout_seconds):
                response = await self.client.post(self.settings.reranker_url, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        except (TimeoutError, httpx.TimeoutException):
            RERANKER_CALLS.labels(outcome="timeout").inc()
            raise ServiceError("重排服务超时，已回退融合排序", code="reranker_unavailable") from None
        except Exception:
            RERANKER_CALLS.labels(outcome="error").inc()
            raise ServiceError("重排服务不可用，已回退融合排序", code="reranker_unavailable") from None
        finally:
            RERANKER_SECONDS.observe(time.monotonic() - started)

        try:
            results = body["results"]
            if not isinstance(results, list) or len(results) > len(rows):
                raise ValueError("results")
            output: list[tuple[int, float]] = []
            seen: set[int] = set()
            for item in results:
                index = item["index"]
                value = item["relevance_score"]
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(rows) or index in seen:
                    raise ValueError("index")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise ValueError("score")
                seen.add(index)
                output.append((index, float(value)))
            if len(output) < min(top_n, len(rows)):
                raise ValueError("incomplete")
        except (KeyError, TypeError, ValueError):
            RERANKER_CALLS.labels(outcome="invalid").inc()
            raise ServiceError("重排服务返回无效，已回退融合排序", code="reranker_unavailable") from None
        RERANKER_CALLS.labels(outcome="success").inc()
        return output[:top_n]
