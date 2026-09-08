"""Explicit multi-agent contracts for the research graph.

The agents share one process and the configured model endpoint, but they do
not share tool permissions. A LangGraph node enters an agent scope before it
can call a model, web search/fetch, local retrieval, or user profile storage.
This makes the collaboration boundary observable and enforceable instead of
being a collection of differently named prompts.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from ..infrastructure.providers import ServiceError


@dataclass(frozen=True)
class AgentSpec:
    id: str
    label: str
    tools: frozenset[str]


AGENTS: dict[str, AgentSpec] = {
    "router": AgentSpec("router", "协调路由 Agent", frozenset({"model", "profile"})),
    "chat": AgentSpec("chat", "对话 Agent", frozenset({"model"})),
    "planner": AgentSpec("planner", "研究规划 Agent", frozenset({"model"})),
    "web_scout": AgentSpec("web_scout", "网络调研 Agent", frozenset({"web_search", "web_fetch"})),
    "local_scout": AgentSpec("local_scout", "本地资料 Agent", frozenset({"local_retrieval"})),
    "judge": AgentSpec("judge", "证据裁决 Agent", frozenset({"model"})),
    "analyst": AgentSpec("analyst", "分析 Agent", frozenset({"model"})),
    "reflect": AgentSpec("reflect", "补搜决策 Agent", frozenset({"model"})),
    "writer": AgentSpec("writer", "报告撰写 Agent", frozenset({"model"})),
    "validator": AgentSpec("validator", "引用核验 Agent", frozenset({"model"})),
}


RECIPIENTS: dict[str, tuple[str, ...]] = {
    "router": ("chat", "planner"),
    "chat": (),
    "planner": ("web_scout", "local_scout"),
    "web_scout": ("judge",),
    "local_scout": ("judge",),
    "judge": ("analyst",),
    "analyst": ("reflect",),
    "reflect": ("web_scout", "local_scout", "writer"),
    "writer": ("validator",),
    "validator": (),
}


class AgentRuntime:
    """Tracks the active Agent and rejects out-of-contract tool calls."""

    def __init__(self, db):
        self.db = db
        self._active: ContextVar[str | None] = ContextVar("active_research_agent", default=None)

    @contextmanager
    def activate(self, agent_id: str):
        if agent_id not in AGENTS:
            raise ValueError(f"未知研究 Agent：{agent_id}")
        token = self._active.set(agent_id)
        try:
            yield AGENTS[agent_id]
        finally:
            self._active.reset(token)

    def require(self, tool: str) -> None:
        agent_id = self._active.get()
        # Direct method calls in focused unit tests remain possible. Runtime
        # graph execution always has a scope and is therefore enforced.
        if agent_id is None:
            return
        if tool not in AGENTS[agent_id].tools:
            raise ServiceError(f"{AGENTS[agent_id].label} 无权调用 {tool}")

    async def started(self, run_id: str, agent_id: str, round_number: int) -> None:
        spec = AGENTS[agent_id]
        await self.db.event(run_id, "agent_start", {"agent": agent_id, "label": spec.label,
                                                     "tools": sorted(spec.tools), "round": round_number})

    async def handoff(self, run_id: str, agent_id: str, result: dict) -> None:
        await self.db.event(run_id, "agent_handoff", {"from": agent_id, "to": list(RECIPIENTS[agent_id]),
                                                        "artifacts": sorted(result.keys())})
