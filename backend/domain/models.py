from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=4000)
    thread_id: str | None = None
    mode: Literal["auto", "quick", "deep"] = "auto"
    client_request_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")


class MemoryRequest(BaseModel):
    content: str = Field(min_length=1, max_length=1000)


class DocumentSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=6, ge=1, le=20)


class ThreadRequest(BaseModel):
    title: str = Field(default="新的研究", min_length=1, max_length=100)

class WorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class MembershipRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:@-]+$")
    role: Literal["admin", "researcher", "viewer"]


class WorkspaceLimitRequest(BaseModel):
    daily_search_limit: int = Field(ge=1, le=10000)
    concurrent_run_limit: int = Field(ge=1, le=20)


class Route(BaseModel):
    mode: Literal["chat", "quick", "deep"]
    reason: str = Field(max_length=300)
    memory_action: Literal["none", "set_assistant_name", "get_assistant_name", "get_preferences", "get_previous_topic"] = "none"
    assistant_name: str = Field(default="", max_length=24)


class ChatAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


class Plan(BaseModel):
    title: str = Field(max_length=160)
    questions: list[str] = Field(min_length=1, max_length=5)
    queries: list[str] = Field(min_length=1, max_length=4)
    scope: str = Field(max_length=1000)


class Evidence(BaseModel):
    id: str
    kind: Literal["web", "local"]
    title: str
    url: str = ""
    text: str
    published_at: str = ""
    retrieved_at: str = ""
    locator: str = ""
    document_id: str = ""
    content_hash: str = ""
    access: Literal["fulltext", "summary", "document"] = "summary"
    domain: str = ""
    evidence_level: Literal["primary", "secondary", "local"] = "secondary"
    trust_label: str = ""


class Judgment(BaseModel):
    accepted_ids: list[str]
    conflicts: list[str] = Field(default_factory=list, max_length=12)
    notes: list[str] = Field(default_factory=list, max_length=12)


class Claim(BaseModel):
    text: str = Field(max_length=1500)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class Analysis(BaseModel):
    claims: list[Claim] = Field(default_factory=list, max_length=40)
    gaps: list[str] = Field(default_factory=list, max_length=8)


class Reflection(BaseModel):
    queries: list[str] = Field(default_factory=list, max_length=4)
    reason: str = Field(max_length=500)


class ReportSection(BaseModel):
    heading: str = Field(max_length=150)
    claims: list[Claim] = Field(default_factory=list, max_length=12)


class ReportDraft(BaseModel):
    title: str = Field(max_length=160)
    sections: list[ReportSection] = Field(min_length=1, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=12)


class ClaimCheck(BaseModel):
    index: int = Field(ge=0)
    supported: bool
    reason: str = Field(max_length=400)


class Verification(BaseModel):
    checks: list[ClaimCheck] = Field(default_factory=list)


class ResearchState(TypedDict, total=False):
    run_id: str
    user_id: str
    owner_subject: str
    thread_id: str
    topic: str
    requested_mode: str
    mode: str
    context: str
    plan: dict
    queries: list[str]
    searched: list[str]
    round: int
    web_results: list[dict]
    local_results: list[dict]
    evidence: list[dict]
    conflicts: list[str]
    claims: list[dict]
    gaps: list[str]
    reflect: bool
    report: str
    draft: dict
    validation: dict
    limitations: list[str]
