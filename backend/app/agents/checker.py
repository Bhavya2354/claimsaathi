"""Checker: nothing reaches the user without passing it.

Deterministic checks come first and are cheap: every clause ID cited must be one
the agent was shown, every rupee figure in a letter must come from the engine
or the insurer's letter, and filled terms must carry a citation. A fast model
then checks that the cited clauses actually support what was said.
"""

import re
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, Field

from app.agents.advocate import LetterDraft, ReviewResult
from app.agents.context import pack_clauses
from app.agents.policy_expert import PolicyAnswer
from app.config import RetrievalSettings
from app.llm.prompting import document_block, system_prompt
from app.llm.router import ModelRouter
from app.llm.types import Message, Task
from app.policy.chunking import Clause
from app.rules.terms import PolicyTerms

_RUPEES = re.compile(r"(?:rs\.?|inr|₹)\s?([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
_TOLERANCE = Decimal(1)
# A letter shorter than this is a stub, not a case.
_MIN_LETTER_CHARS = 300

_SUPPORT_ROLE = """You verify answers about health insurance policies. Given policy clauses and
a statement that cites them, list each claim in the statement that the clauses do not support
or that contradicts them. Paraphrase is fine; only flag what is unsupported or wrong."""


class CheckReport(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)


class _Support(BaseModel):
    unsupported: list[str] = Field(default_factory=list)


def _unknown_ids(cited: Iterable[str], allowed: set[str]) -> list[str]:
    return sorted({c for c in cited if c not in allowed})


def _terms_issues(terms: PolicyTerms, allowed: set[str]) -> list[str]:
    issues: list[str] = []
    cited: list[str] = [*terms.clause_ids, *terms.proportionate_clause_ids]
    named = {
        "room rent limit": terms.room_rent,
        "ICU limit": terms.icu_rent,
        "co-payment": terms.copay,
        "deductible": terms.deductible,
    }
    for label, term in named.items():
        if term is None:
            continue
        cited.extend(term.clause_ids)
        if not term.clause_ids:
            issues.append(f"The {label} has no clause citation.")
    for period in terms.waiting_periods:
        cited.extend(period.clause_ids)
        if not period.clause_ids:
            issues.append(f"The {period.kind.value} waiting period has no clause citation.")
    for sub in terms.sub_limits:
        cited.extend(sub.clause_ids)
        if not sub.clause_ids:
            issues.append(f"The sub-limit for {sub.treatment} has no clause citation.")
    if terms.sum_insured is not None and not terms.clause_ids:
        issues.append("The sum insured has no clause citation.")
    if unknown := _unknown_ids(cited, allowed):
        issues.append(f"Cited clause IDs that were not provided: {', '.join(unknown)}.")
    return issues


def letter_amount_issues(body: str, allowed: set[Decimal]) -> list[str]:
    issues: list[str] = []
    for match in _RUPEES.finditer(body):
        try:
            value = Decimal(match.group(1).replace(",", ""))
        except InvalidOperation:
            continue
        if not any(abs(value - known) <= _TOLERANCE for known in allowed):
            issues.append(
                f"The letter states Rs {match.group(1)}, which is not in the amount table."
            )
    return issues


class Checker:
    def __init__(self, router: ModelRouter, settings: RetrievalSettings) -> None:
        self._router = router
        self._settings = settings

    def terms(self, terms: PolicyTerms, shown: Sequence[Clause]) -> CheckReport:
        issues = _terms_issues(terms, {c.clause_id for c in shown})
        return CheckReport(passed=not issues, issues=issues)

    async def answer(
        self, answer: PolicyAnswer, shown: Sequence[Clause], tags: dict[str, str]
    ) -> CheckReport:
        allowed = {c.clause_id for c in shown}
        issues: list[str] = []
        if unknown := _unknown_ids(answer.clause_ids, allowed):
            issues.append(f"Cited clause IDs that were not provided: {', '.join(unknown)}.")
        if answer.found_in_policy and not answer.clause_ids:
            issues.append("The answer cites no clause.")
        if not issues and answer.found_in_policy:
            issues.extend(await self._support(answer.answer, answer.clause_ids, shown, tags))
        return CheckReport(passed=not issues, issues=issues)

    async def review(
        self, review: ReviewResult, shown: Sequence[Clause], tags: dict[str, str]
    ) -> CheckReport:
        allowed = {c.clause_id for c in shown}
        cited = [cid for reason in review.reasons for cid in reason.clause_ids]
        issues: list[str] = []
        if unknown := _unknown_ids(cited, allowed):
            issues.append(f"Cited clause IDs that were not provided: {', '.join(unknown)}.")
        for reason in review.reasons:
            if reason.verdict.value in {"supported", "not_supported"} and not reason.clause_ids:
                issues.append(f"The verdict on '{reason.insurer_reason[:60]}' cites no clause.")
        if not issues:
            statement = "\n".join(
                f"{r.insurer_reason}: {r.verdict.value}. {r.explanation}" for r in review.reasons
            )
            issues.extend(await self._support(statement, cited, shown, tags))
        return CheckReport(passed=not issues, issues=issues)

    def letter(
        self, letter: LetterDraft, shown: Sequence[Clause], amounts: set[Decimal]
    ) -> CheckReport:
        issues: list[str] = []
        if unknown := _unknown_ids(letter.clause_ids, {c.clause_id for c in shown}):
            issues.append(f"Cited clause IDs that were not provided: {', '.join(unknown)}.")
        issues.extend(letter_amount_issues(letter.body, amounts))
        if len(letter.body) < _MIN_LETTER_CHARS:
            issues.append("The letter is too short to state the case.")
        return CheckReport(passed=not issues, issues=issues)

    async def _support(
        self,
        statement: str,
        cited: Sequence[str],
        shown: Sequence[Clause],
        tags: dict[str, str],
    ) -> list[str]:
        wanted = set(cited)
        context, _ = pack_clauses(
            (c for c in shown if c.clause_id in wanted), self._settings.context_max_chars
        )
        if not context:
            return []
        verdict = await self._router.structured(
            Task.FAST,
            [
                Message(role="system", content=system_prompt(_SUPPORT_ROLE)),
                Message(
                    role="user",
                    content=f"Clauses:\n{context}\n\n{document_block(statement, 'statement')}",
                ),
            ],
            _Support,
            tags={**tags, "agent": "checker"},
        )
        return [f"Not supported by the cited clauses: {claim}" for claim in verdict.unsupported]
