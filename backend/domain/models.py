from typing import Literal, TypedDict

from pydantic import BaseModel, Field, model_validator, model_serializer


class RunRequest(BaseModel):
    data_policy: Literal["public", "internal", "restricted"] = "internal"
    topic: str = Field(default="", max_length=4000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def valid_input(self):
        self.topic = self.topic.strip()
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("图片不能重复")
        if not self.topic and not self.attachment_ids:
            raise ValueError("请输入文字或上传图片")
        if not self.topic:
            self.topic = "描述图片并提取关键信息"
        return self

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
    # Legacy clients may send this field; daily quotas are no longer enforced.
    daily_search_limit: int | None = Field(default=None, deprecated=True)
    concurrent_run_limit: int = Field(ge=1, le=20)


class Route(BaseModel):
    mode: Literal["chat", "quick", "deep"]
    reason: str = Field(max_length=300)
    memory_action: Literal["none", "set_assistant_name", "get_assistant_name", "get_preferences", "get_previous_topic"] = "none"
    assistant_name: str = Field(default="", max_length=24)


class ChatAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


class ImageObservation(BaseModel):
    index: int = Field(ge=1, le=4)
    readable: bool
    text: str = Field(max_length=6000)


class VisionResult(BaseModel):
    mode: Literal["chat", "quick", "deep"]
    answer: str = Field(min_length=1, max_length=8000)
    observations: list[ImageObservation] = Field(min_length=1, max_length=4)


class Plan(BaseModel):
    title: str = Field(max_length=160)
    questions: list[str] = Field(min_length=1, max_length=5)
    queries: list[str] = Field(min_length=1, max_length=4)
    scope: str = Field(max_length=1000)


class Evidence(BaseModel):
    index_version: int = 0
    chunk_id: str = ""
    document_hash: str = ""
    id: str
    kind: Literal["web", "local", "attachment"]
    attachment_id: str = ""
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
    conflicts: list[str] = Field(default_factory=list, max_length=32)
    notes: list[str] = Field(default_factory=list, max_length=32)


class Claim(BaseModel):
    kind: Literal["fact", "inference", "unspecified"] = "unspecified"

    @model_serializer(mode="wrap")
    def serialize_claim(self, handler):
        data = handler(self)
        if self.kind == "unspecified":
            data.pop("kind", None)
        return data

    text: str = Field(max_length=1500)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class Analysis(BaseModel):
    claims: list[Claim] = Field(default_factory=list, max_length=40)
    gaps: list[str] = Field(default_factory=list, max_length=8)


class SourceFact(BaseModel):
    source_id: str
    quote: str = Field(min_length=1, max_length=1400)


class SourceFacts(BaseModel):
    facts: list[SourceFact] = Field(default_factory=list, max_length=16)


class OutlineSection(BaseModel):
    question_index: int | None = Field(default=None, ge=0, le=4)
    claim_indices: list[int] = Field(default_factory=list, max_length=40)


class ReportOutline(BaseModel):
    sections: list[OutlineSection] = Field(min_length=1, max_length=8)


class QuestionCoverage(BaseModel):
    original_request_answered: bool = False
    answered_questions: list[int] = Field(default_factory=list, max_length=5)


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


class ClaimReplacement(BaseModel):
    index: int = Field(ge=0)
    replacement: Claim | None = None


class ReportRepair(BaseModel):
    repairs: list[ClaimReplacement] = Field(default_factory=list, max_length=96)


class AtomicClaimCheck(BaseModel):
    text: str = Field(min_length=1, max_length=1500,
                      description="按顺序逐字摘录待核验claim中的连续原文片段，禁止从来源补写或改写")
    supported: bool
    target_id: str | None = None


class ClaimCheck(BaseModel):
    atomic_checks: list[AtomicClaimCheck] = Field(default_factory=list, max_length=12)
    unsupported_parts: list[str] = Field(default_factory=list, max_length=12)
    omitted_conditions: list[str] = Field(default_factory=list, max_length=12)
    index: int = Field(ge=0)
    supported: bool
    reason: str = Field(max_length=4000)
    verification_complete: bool = True
    format_error: str = ""


class Verification(BaseModel):
    checks: list[ClaimCheck] = Field(default_factory=list)
    answered_questions: list[int] = Field(default_factory=list, max_length=5)


class TargetSupport(BaseModel):
    target_id: str = ""
    supported: bool | None = None
    reason: str = Field(default="", max_length=2000)


class TargetClaimCheck(BaseModel):
    index: int | None = None
    atomic_checks: list[TargetSupport] = Field(default_factory=list, max_length=12)
    unsupported_parts: list[str] = Field(default_factory=list, max_length=12)
    omitted_conditions: list[str] = Field(default_factory=list, max_length=12)
    supported: bool | None = None
    reason: str = Field(default="", max_length=4000)


class TargetVerification(BaseModel):
    """Wire verdict: the model selects immutable targets, never rewrites them."""
    checks: list[TargetClaimCheck] = Field(default_factory=list)


class ResearchState(TypedDict, total=False):
    attachment_ids: list[str]
    attachment_evidence: list[dict]
    vision: dict
    web_outcomes: list[dict]
    local_outcomes: list[dict]
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
    final_claims: list[dict]
    corpus_revision: int
    revision_plan: dict
    recovered_claims: list[dict]
    analysis_input_hash: str
    gaps: list[str]
    reflect: bool
    report: str
    draft: dict
    validation: dict
    limitations: list[str]
