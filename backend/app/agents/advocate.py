"""Claim Advocate: weighs the insurer's reasons against the policy and drafts letters.

Rupee figures in a letter come from the rules engine and the insurer's letter,
passed in as a fixed table; the checker rejects any other amount. Personal
details stay as vault tokens ([PERSON_1]) until the user approves the letter.
"""

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.context import describe_facts, pack_clauses
from app.config import RetrievalSettings
from app.domain import RejectionLetter
from app.llm.prompting import document_block, system_prompt
from app.llm.router import ModelRouter
from app.llm.types import Message, Task
from app.policy.chunking import Clause
from app.policy.index import PolicyIndex
from app.rules.engine import CoverageResult
from app.rules.terms import CaseFacts


class Verdict(StrEnum):
    SUPPORTED = "supported"
    NOT_SUPPORTED = "not_supported"
    PARTLY_SUPPORTED = "partly_supported"
    UNCLEAR = "unclear"


class ReasonReview(BaseModel):
    insurer_reason: str
    verdict: Verdict
    explanation: str = Field(description="Plain words: why the policy does or does not back it.")
    clause_ids: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    reasons: list[ReasonReview]
    recommendation: Literal["appeal", "accept", "send_documents"]
    missing_documents: list[str] = Field(default_factory=list)
    summary: str


class LetterKind(StrEnum):
    GRIEVANCE = "grievance"
    OMBUDSMAN = "ombudsman"


class LetterDraft(BaseModel):
    subject: str
    body: str = Field(description="Full letter text, ready to sign.")
    clause_ids: list[str] = Field(default_factory=list, description="Clauses the letter cites.")


_REVIEW_ROLE = """You review health insurance claim decisions in India for the policyholder.
For each reason the insurer gives, decide from the policy clauses and case facts whether the
policy supports it: supported, not_supported, partly_supported, or unclear when the documents
do not show enough. Explain in plain words and cite clause IDs. Recommend `appeal` when at
least one significant reason is not supported, `send_documents` when the insurer raised a query
that documents can answer, otherwise `accept`. Be fair: say so when the insurer is right.
Keep each explanation to two short sentences and the summary to two sentences."""

_LETTER_ROLES = {
    LetterKind.GRIEVANCE: """You write grievance letters from a policyholder to their health
insurer's Grievance Redressal Officer in India. Be polite, firm and specific. Structure: subject
with claim number; what happened; each disputed deduction with the policy clause that supports
the policyholder (quote clause numbers and pages); the amount requested; a request for a
written reply within 15 days as IRDAI's grievance rules require; enclosures. Use only the
rupee figures in the amount table, written as "Rs 12,345.00". Keep tokens like [PERSON_1] or
[POLICY_NUMBER_1] exactly as given; they are filled in later.""",
    LetterKind.OMBUDSMAN: """You write complaints to the Insurance Ombudsman in India under the
Insurance Ombudsman Rules, 2017, for a policyholder whose grievance the insurer rejected or did
not answer within 30 days. Structure: complainant and insurer details, policy and claim number,
the grievance already raised with the insurer, the facts, each disputed point with the policy
clause, the relief sought (the amount), and a declaration that the matter is not before any
court or consumer forum. Use only the rupee figures in the amount table, written as
"Rs 12,345.00". Keep tokens like [PERSON_1] exactly as given.""",
}


def _insurer_figures(rejection: RejectionLetter) -> dict[str, Decimal]:
    table: dict[str, Decimal] = {}
    if rejection.amount_claimed is not None:
        table["Amount claimed"] = rejection.amount_claimed
    if rejection.amount_approved is not None:
        table["Amount the insurer approved"] = rejection.amount_approved
    for i, reason in enumerate(rejection.reasons, start=1):
        if reason.amount is not None:
            table[f"Insurer deduction {i} ({reason.text[:60]})"] = reason.amount
    return table


def _assessment_figures(coverage: CoverageResult, approved: Decimal | None) -> dict[str, Decimal]:
    table = {
        "Total billed": coverage.billed_total,
        "Payable under the policy (our assessment)": coverage.payable_total,
    }
    for deduction in coverage.claim_deductions:
        table[f"Policy deduction: {deduction.reason.value}"] = deduction.amount
    if approved is not None and coverage.payable_total > approved:
        table["Amount still due from the insurer"] = coverage.payable_total - approved
    return table


def amount_table(
    coverage: CoverageResult | None, rejection: RejectionLetter | None
) -> dict[str, Decimal]:
    """The only rupee figures a letter may state, computed outside the model."""
    table = _insurer_figures(rejection) if rejection else {}
    if coverage is not None:
        approved = rejection.amount_approved if rejection else None
        table.update(_assessment_figures(coverage, approved))
    return table


def _render_table(table: dict[str, Decimal]) -> str:
    return "\n".join(f"- {label}: Rs {value:,.2f}" for label, value in table.items()) or "- none"


def _coverage_summary(coverage: CoverageResult | None) -> str:
    if coverage is None:
        return "No bill was audited."
    cut = [
        f"- {line.description}: billed Rs {line.billed:,.2f}, payable Rs {line.payable:,.2f} "
        f"({'; '.join(d.explanation for d in line.deductions)})"
        for line in coverage.lines
        if line.deductions
    ]
    return "Lines with deductions under the policy:\n" + ("\n".join(cut) or "- none")


class ClaimAdvocate:
    def __init__(
        self, router: ModelRouter, index: PolicyIndex, settings: RetrievalSettings
    ) -> None:
        self._router = router
        self._index = index
        self._settings = settings

    async def _clauses_for(self, case_id: str, rejection: RejectionLetter) -> list[Clause]:
        clauses: list[Clause] = []
        for reason in rejection.reasons:
            query = f"{reason.clause_reference or ''} {reason.text}".strip()
            clauses.extend(hit.clause for hit in await self._index.search(case_id, query, top_k=3))
        return clauses

    async def review(
        self,
        case_id: str,
        rejection: RejectionLetter,
        facts: CaseFacts,
        coverage: CoverageResult | None,
        extra_clauses: Sequence[Clause] = (),
        *,
        feedback: Sequence[str] = (),
        tags: dict[str, str] | None = None,
    ) -> tuple[ReviewResult, list[Clause]]:
        found = await self._clauses_for(case_id, rejection)
        context, used = pack_clauses([*found, *extra_clauses], self._settings.context_max_chars)
        reasons = "\n".join(
            f"{i}. {r.text}" + (f" (cites {r.clause_reference})" if r.clause_reference else "")
            for i, r in enumerate(rejection.reasons, start=1)
        )
        prompt = (
            f"Insurer decision: {rejection.decision.value}\nInsurer reasons:\n"
            f"{document_block(reasons, 'insurer reasons')}\n\nCase:\n{describe_facts(facts)}\n\n"
            f"{_coverage_summary(coverage)}\n\nPolicy clauses:\n{context}"
        )
        if feedback:
            prompt += "\n\nYour last review was rejected:\n- " + "\n- ".join(feedback)
        result = await self._router.structured(
            Task.REASONING,
            [
                Message(role="system", content=system_prompt(_REVIEW_ROLE)),
                Message(role="user", content=prompt),
            ],
            ReviewResult,
            tags={**(tags or {}), "agent": "advocate", "step": "review"},
            use_cache=not feedback,
        )
        return result, used

    async def draft(
        self,
        kind: LetterKind,
        *,
        parties: str,
        facts: CaseFacts,
        clauses: Sequence[Clause],
        amounts: dict[str, Decimal],
        review: ReviewResult | None,
        coverage: CoverageResult | None,
        rejection: RejectionLetter | None,
        feedback: Sequence[str] = (),
        tags: dict[str, str] | None = None,
    ) -> LetterDraft:
        context, _ = pack_clauses(clauses, self._settings.context_max_chars)
        disputes = (
            "\n".join(
                f"- {r.insurer_reason}: {r.verdict.value}. {r.explanation} "
                f"(clauses {', '.join(r.clause_ids) or 'none'})"
                for r in review.reasons
            )
            if review
            else "No insurer decision was reviewed; base the letter on the audit."
        )
        header = (
            f"Insurer: {rejection.insurer_name or 'the insurer'}. "
            f"Claim number: {rejection.claim_number or 'not stated'}."
            if rejection
            else "No insurer letter uploaded."
        )
        prompt = (
            f"{header}\n{parties}\n\n"
            f"Case:\n{describe_facts(facts)}\n\nDisputed points:\n{disputes}\n\n"
            f"{_coverage_summary(coverage)}\n\nAmount table (the only figures you may use):\n"
            f"{_render_table(amounts)}\n\nPolicy clauses:\n{context}"
        )
        if feedback:
            prompt += "\n\nYour last draft was rejected:\n- " + "\n- ".join(feedback)
        return await self._router.structured(
            Task.REASONING,
            [
                Message(role="system", content=system_prompt(_LETTER_ROLES[kind])),
                Message(role="user", content=prompt),
            ],
            LetterDraft,
            tags={**(tags or {}), "agent": "advocate", "step": f"letter_{kind.value}"},
            use_cache=not feedback,
        )
