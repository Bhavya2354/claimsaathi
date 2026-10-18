"""Read models returned by the API. Built from masked records, then unmasked for the owner."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.advocate import ReviewResult
from app.agents.graph import Intent
from app.domain import RejectionDecision
from app.policy.chunking import Clause
from app.rules.checklist import Checklist
from app.rules.engine import CoverageResult
from app.rules.terms import CaseFacts, PolicyTerms


class ClauseView(BaseModel):
    clause_id: str
    section: str
    citation: str
    text: str

    @classmethod
    def of(cls, clause: Clause) -> "ClauseView":
        return cls(
            clause_id=clause.clause_id,
            section=clause.section,
            citation=clause.citation,
            text=clause.text,
        )


class Verification(BaseModel):
    verified: bool
    issues: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)


class AuditView(Verification):
    analysis_id: str
    source: Literal["bill", "estimate", "form"]
    coverage: CoverageResult
    terms: PolicyTerms
    facts: CaseFacts
    clauses: list[ClauseView]
    warnings: list[str] = Field(default_factory=list)
    reasoning: str | None = None


class RejectionSummary(BaseModel):
    insurer_name: str | None
    claim_number: str | None
    decision: RejectionDecision
    amount_claimed: float | None
    amount_approved: float | None


class ReviewView(Verification):
    analysis_id: str
    review: ReviewResult
    rejection: RejectionSummary
    coverage: CoverageResult | None
    clauses: list[ClauseView]


class LetterView(Verification):
    id: str
    kind: str
    subject: str
    body: str
    status: Literal["draft", "approved"]
    clauses: list[ClauseView] = Field(default_factory=list)
    created_at: datetime
    approved_at: datetime | None = None


class ChatReply(Verification):
    intent: Intent
    text: str
    clauses: list[ClauseView] = Field(default_factory=list)
    audit: AuditView | None = None
    review: ReviewView | None = None
    letter: LetterView | None = None
    checklist: Checklist | None = None


class DocumentView(BaseModel):
    id: str
    filename: str
    kind: str | None
    status: Literal["processing", "ready", "failed"]
    error: str | None
    page_count: int | None
    text_sources: list[str] | None
    clause_count: int | None
    summary: dict[str, Any] | None
    created_at: datetime


class MessageView(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    verified: bool | None
    created_at: datetime


class CaseView(BaseModel):
    id: str
    expires_at: datetime
    facts: dict[str, Any]
    effective_facts: CaseFacts
    documents: list[DocumentView]
    letters: list[LetterView]
    messages: list[MessageView]
    latest_audit_id: str | None
    latest_review_id: str | None


class CreatedCase(BaseModel):
    id: str
    token: str = Field(description="Shown once. Send as `Authorization: Bearer <token>`.")
    expires_at: datetime
