import pytest

from backend.infrastructure.providers import ServiceError
from backend.research.agents import AGENTS, AgentRuntime


class EventStore:
    def __init__(self):
        self.events = []

    async def event(self, run_id, event_type, data):
        self.events.append((run_id, event_type, data))


def test_agent_contract_rejects_out_of_scope_tool_calls():
    runtime = AgentRuntime(EventStore())
    with runtime.activate("planner"):
        runtime.require("model")
        with pytest.raises(ServiceError, match="无权"):
            runtime.require("web_search")
    with runtime.activate("web_scout"):
        runtime.require("web_search")
        runtime.require("web_fetch")
        with pytest.raises(ServiceError, match="无权"):
            runtime.require("model")


@pytest.mark.asyncio
async def test_agent_handoff_events_include_contract_and_artifacts():
    store = EventStore()
    runtime = AgentRuntime(store)
    await runtime.started("run-1", "planner", 0)
    await runtime.handoff("run-1", "planner", {"plan": {}, "queries": ["q"]})
    assert store.events[0][1] == "agent_start"
    assert store.events[0][2]["tools"] == ["model"]
    assert store.events[1][2] == {"from": "planner", "to": ["web_scout", "local_scout"],
                                  "artifacts": ["plan", "queries"]}
    assert set(AGENTS) >= {"router", "planner", "web_scout", "local_scout", "judge", "validator"}
