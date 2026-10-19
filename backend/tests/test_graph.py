"""Supervisor routing and the checker's retry loop, with stub agents (no model calls)."""

from decimal import Decimal
from typing import Any

from app.agents.advocate import LetterDraft, LetterKind, ReviewResult
from app.agents.auditor import AuditOutcome
from app.agents.checker import CheckReport
from app.agents.graph import CaseSnapshot, ClaimGraph, Intent
from app.agents.policy_expert import CaseRelation, TermsReading
from app.config import AgentSettings
from app.domain import Bill, BillLine, LineCategory
from app.policy.chunking import Clause
from app.rules.engine import assess
from app.rules.terms import CaseFacts, LabelledLine, PolicyTerms, RoomLimit

CLAUSE = Clause("c-001", "4.1 Room Rent", 2, 2, "Room rent up to 1% of SI per day.")


class StubExpert:
    def __init__(self) -> None:
        self.feedback: list[list[str]] = []

    async def read_terms(self, case_id: str, facts: CaseFacts, **kw: Any) -> TermsReading:
        self.feedback.append(list(kw.get("feedback", [])))
        terms = PolicyTerms(
            sum_insured=Decimal(500_000),
            clause_ids=["c-001"],
            room_rent=RoomLimit(
                basis="percent_of_sum_insured", value=Decimal(1), clause_ids=["c-001"]
            ),
        )
        relation = CaseRelation(treatment_is_pre_existing=False, reasoning="Not pre-existing.")
        return TermsReading(terms=terms, clauses=[CLAUSE], relation=relation)


class StubAuditor:
    async def audit(
        self, bill: Bill, terms: PolicyTerms, facts: CaseFacts, **kw: Any
    ) -> AuditOutcome:
        lines = [LabelledLine(line=line, category=LineCategory.PHARMACY) for line in bill.lines]
        return AuditOutcome(lines=lines, coverage=assess(terms, facts, lines))


class FlakyChecker:
    """Rejects the first reading of the terms, accepts the second."""

    def __init__(self) -> None:
        self.calls = 0

    def terms(self, terms: PolicyTerms, shown: list[Clause]) -> CheckReport:
        self.calls += 1
        if self.calls == 1:
            return CheckReport(passed=False, issues=["The room rent limit has no citation."])
        return CheckReport(passed=True)


def graph(expert: StubExpert, checker: FlakyChecker) -> ClaimGraph:
    return ClaimGraph(
        router=None,  # type: ignore[arg-type]
        expert=expert,  # type: ignore[arg-type]
        auditor=StubAuditor(),  # type: ignore[arg-type]
        advocate=None,  # type: ignore[arg-type]
        checker=checker,  # type: ignore[arg-type]
        settings=AgentSettings(checker_max_retries=2),
    )


def snapshot(**kw: Any) -> CaseSnapshot:
    bill = Bill(lines=[BillLine(description="Pharmacy", amount=Decimal(1_000))])
    kw.setdefault("has_policy", True)
    return CaseSnapshot(case_id="c", facts=CaseFacts(), bill=bill, **kw)


async def test_checker_feedback_reaches_the_agent_and_the_retry_passes() -> None:
    expert, checker = StubExpert(), FlakyChecker()
    state = await graph(expert, checker).run(snapshot(), Intent.AUDIT)
    assert state["report"].passed
    assert state["attempts"] == 2
    assert expert.feedback == [[], ["The room rent limit has no citation."]]
    assert state["audit"].coverage.payable_total == Decimal("1000.00")
    assert state["steps"][0] == "Supervisor: audit requested"
    assert state["steps"].count("Checker: found 1 issues") == 1


async def test_cached_terms_skip_the_policy_reading() -> None:
    expert, checker = StubExpert(), FlakyChecker()
    checker.calls = 1  # accept straight away
    cached = await StubExpert().read_terms("c", CaseFacts())
    state = await graph(expert, checker).run(snapshot(reading=cached), Intent.AUDIT)
    assert expert.feedback == []
    assert state["report"].passed


async def test_missing_documents_stop_before_any_agent() -> None:
    expert = StubExpert()
    state = await graph(expert, FlakyChecker()).run(snapshot(has_policy=False), Intent.AUDIT)
    assert "upload your policy" in state["message"].lower()
    assert expert.feedback == []


async def test_review_needs_an_insurer_letter() -> None:
    state = await graph(StubExpert(), FlakyChecker()).run(snapshot(), Intent.REVIEW)
    assert "insurer's letter" in state["message"]


async def test_checklist_needs_no_model() -> None:
    facts = CaseFacts(claim_type="cashless", is_accident=True)
    state = await graph(StubExpert(), FlakyChecker()).run(
        CaseSnapshot(case_id="c", facts=facts, has_policy=False), Intent.CHECKLIST
    )
    groups = {item.group for item in state["checklist"].items}
    assert groups == {"common", "cashless", "accident"}


async def test_terms_are_prepared_with_the_checker_loop() -> None:
    expert, checker = StubExpert(), FlakyChecker()
    reading = await graph(expert, checker).read_terms(snapshot())
    assert reading is not None
    assert expert.feedback == [[], ["The room rent limit has no citation."]]


class StubAdvocate:
    def __init__(self) -> None:
        self.reviews = 0

    async def review(self, *args: Any, **kw: Any) -> Any:
        self.reviews += 1
        raise AssertionError("a saved review should be reused")

    async def draft(self, *args: Any, **kw: Any) -> LetterDraft:
        return LetterDraft(subject="Appeal", body="x" * 400, clause_ids=["c-001"])


async def test_letters_reuse_a_saved_review() -> None:
    from app.domain import RejectionDecision, RejectionLetter

    checker = FlakyChecker()
    checker.calls = 1
    checker.letter = lambda *a, **k: CheckReport(passed=True)  # type: ignore[attr-defined]
    advocate = StubAdvocate()
    claim_graph = ClaimGraph(
        router=None,  # type: ignore[arg-type]
        expert=StubExpert(),  # type: ignore[arg-type]
        auditor=StubAuditor(),  # type: ignore[arg-type]
        advocate=advocate,  # type: ignore[arg-type]
        checker=checker,  # type: ignore[arg-type]
        settings=AgentSettings(),
    )
    saved = ReviewResult(reasons=[], recommendation="appeal", summary="Appeal the cut.")
    rejection = RejectionLetter(decision=RejectionDecision.PARTIALLY_APPROVED)
    state = await claim_graph.run(
        snapshot(rejection=rejection, review=saved), Intent.LETTER, letter_kind=LetterKind.GRIEVANCE
    )
    assert advocate.reviews == 0
    assert state["letter"].subject == "Appeal"
    assert "reused the saved review" in " ".join(state["steps"])
