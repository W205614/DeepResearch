"""Versioned Java-to-Python command boundary, including legacy outbox replay."""
from typing import Literal
import time

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, Field

VERSION = "research-command-v1"


class ScheduleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["research-command-v1"] = VERSION
    resume: StrictBool = False
    expected_attempt: StrictInt | None = Field(default=None, ge=0, le=2147483647)


def parse_schedule(payload: dict) -> ScheduleCommand:
    # Persisted pre-upgrade outbox rows contain only `resume`. Keep those replayable;
    # a supplied unknown version or lossy bool/int coercion must fail closed.
    if 'contract_version' not in payload and set(payload) - {'resume'}:
        raise ValueError('version required for new command fields')
    if 'expected_attempt' in payload and payload['expected_attempt'] is None:
        raise ValueError('expected_attempt must be an integer')
    return ScheduleCommand.model_validate(payload)


def can_resume(row: dict, settings=None, *, epoch=None) -> bool:
    if row['status'] not in {'failed', 'interrupted', 'cancelled'}:
        return False
    if row.get('error_info', {}).get('retryable') is False:
        return False
    deadline = row.get('deadline_at', 0)
    if deadline and deadline <= (time.time() if epoch is None else epoch):
        return False
    if settings and (row.get('call_attempts', 0) >= settings.max_run_call_attempts
                     or row.get('reserved_tokens', 0) >= settings.max_run_reserved_tokens):
        return False
    return True
