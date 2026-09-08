"""Only explicit user-selected research modes bypass semantic routing."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RuleDecision:
    mode: str
    reason: str
    signals: tuple[str, ...]


def decide(requested_mode: str, _topic: str) -> RuleDecision | None:
    """Honor a manual research-mode selection; natural language goes to the router model.

    The router receives the current Thread context and decides whether the request
    needs external evidence or can be answered from conversation/profile memory.
    """
    if requested_mode in {"quick", "deep"}:
        return RuleDecision(requested_mode, "按用户指定模式执行", ("explicit_mode",))
    return None
