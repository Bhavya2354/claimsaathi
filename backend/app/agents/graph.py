"""Supervisor: a LangGraph state machine that runs the agents a request needs.

    route ─┬─ answer ─────────────────────────────┐
           ├─ checklist ── END                    │
           └─ terms ─┬─ audit ─┬─ review ─┬─ letter ─┤
                     │         │          │          ▼
                     └─────────┴──────────┴──────► check ── END
                                                     │
                     retry with the checker's issues ┘

Routing is code, not a model: the Supervisor only asks a model to classify free
chat messages. The checker sends failed work back to the agent that produced it
with the issues as feedback, at most `checker_max_retries` times; after that the
result is returned marked unverified rather than hidden.
"""

import operator
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from app.agents.advocate import (
    ClaimAdvocate,
    LetterDraft,
    LetterKind,
    ReviewResult,
    amount_table,
)
from app.agents.auditor import AuditOutcome, BillAuditor
from app.agents.checker import Checker, CheckReport
from app.agents.policy_expert import CaseRelation, PolicyAnswer, PolicyExpert, TermsReading
from app.config import AgentSettings
from app.domain import Bill, RejectionLetter
from app.llm.prompting import system_prompt
from app.llm.router import ModelRouter
from app.llm.types import Message, Task
from app.policy.chunking import Clause
from app.rules.checklist import Checklist, build_checklist
from app.rules.terms import CaseFacts, LabelledLine


class Intent(StrEnum):
    ASK = "ask"
    AUDIT = "audit"
    REVIEW = "review"
    LETTER = "letter"
    CHECKLIST = "checklist"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True, slots=True)
class CaseSnapshot:
    """Everything known about a case when a request starts, loaded by the service layer."""

    case_id: str
    facts: CaseFacts
    has_policy: bool
    bill: Bill | None = None
    rejection: RejectionLetter | None = None
    reading: TermsReading | None = None
    labelled: list[LabelledLine] | None = None
    parties: str = "Policyholder: [your name]. Policy number: [your policy number]."


class GraphState(TypedDict, total=False):
    snapshot: CaseSnapshot
    intent: Intent
    question: str
    history: list[Message]
    letter_kind: LetterKind
    facts: CaseFacts
    reading: TermsReading
    relation: CaseRelation
    audit: AuditOutcome
    review: ReviewResult
    review_clauses: list[Clause]
    letter: LetterDraft
    amounts: dict[str, Decimal]
    answer: PolicyAnswer
    answer_clauses: list[Clause]
    checklist: Checklist
    report: CheckReport
    attempts: int
    feedback: list[str]
    retry: str
    message: str
    steps: Annotated[list[str], operator.add]


class _Classified(BaseModel):
    intent: Intent
    letter_kind: LetterKind | None = Field(
        default=None, description="For letters: grievance to insurer, or ombudsman complaint."
    )


_ROUTE_ROLE = """You route messages in a health insurance claim assistant for India.
- ask: a question about the policy, cover, limits, waiting periods, or the claim process
- audit: check the hospital bill or estimate against the policy
- review: check whether the insurer's rejection or deduction is correct
- letter: write a grievance letter to the insurer or an ombudsman complaint
- checklist: which documents are needed for the claim
- out_of_scope: anything else, including medical diagnosis or treatment advice"""

_OUT_OF_SCOPE = (
    "I can help with your health insurance policy, hospital bill, claim decision, appeal "
    "letters and claim documents. For medical advice, please speak to your doctor."
)


def _has_inputs(intent: Intent, snapshot: CaseSnapshot) -> bool:
    if not snapshot.has_policy:
        return False
    if intent == Intent.AUDIT:
        return snapshot.bill is not None
    if intent == Intent.REVIEW:
        return snapshot.rejection is not None
    if intent == Intent.LETTER:
        return snapshot.bill is not None or snapshot.rejection is not None
    return True


class ClaimGraph:
    def __init__(
        self,
        *,
        router: ModelRouter,
        expert: PolicyExpert,
        auditor: BillAuditor,
        advocate: ClaimAdvocate,
        checker: Checker,
        settings: AgentSettings,
    ) -> None:
        self._router = router
        self._expert = expert
        self._auditor = auditor
        self._advocate = advocate
        self._checker = checker
        self._settings = settings
        self._graph = self._build()

    def _build(self) -> CompiledStateGraph[Any]:
        graph = StateGraph(GraphState)
        for name, node in (
            ("route", self._route),
            ("blocked", self._blocked),
            ("checklist", self._checklist),
            ("terms", self._terms),
            ("audit", self._audit),
            ("review", self._review),
            ("letter", self._letter),
            ("answer", self._answer),
            ("check", self._check),
        ):
            graph.add_node(name, node)
        graph.add_edge(START, "route")
        graph.add_conditional_edges(
            "route", self._after_route, ["answer", "checklist", "terms", "blocked", END]
        )
        graph.add_edge("blocked", END)
        graph.add_edge("checklist", END)
        graph.add_conditional_edges("terms", self._after_terms, ["audit", "review", "letter"])
        graph.add_conditional_edges("audit", self._after_audit, ["check", "review", "letter"])
        graph.add_conditional_edges("review", self._after_review, ["check", "letter"])
        graph.add_edge("letter", "check")
        graph.add_edge("answer", "check")
        graph.add_conditional_edges(
            "check", self._after_check, ["terms", "review", "letter", "answer", END]
        )
        return graph.compile()

    async def run(
        self,
        snapshot: CaseSnapshot,
        intent: Intent | None,
        *,
        question: str | None = None,
        history: list[Message] | None = None,
        letter_kind: LetterKind | None = None,
    ) -> GraphState:
        state: GraphState = {
            "snapshot": snapshot,
            "facts": snapshot.facts,
            "history": history or [],
            "attempts": 0,
            "feedback": [],
            "steps": [],
        }
        if intent is not None:
            state["intent"] = intent
        if question is not None:
            state["question"] = question
        if letter_kind is not None:
            state["letter_kind"] = letter_kind
        result = cast(GraphState, await self._graph.ainvoke(state))
        return result

    @staticmethod
    def _tags(state: GraphState) -> dict[str, str]:
        return {"case": state["snapshot"].case_id}

    # --- nodes -----------------------------------------------------------------------------

    async def _route(self, state: GraphState) -> dict[str, Any]:
        if "intent" in state:
            return {"steps": [f"Supervisor: {state['intent'].value} requested"]}
        question = state.get("question", "")
        classified = await self._router.structured(
            Task.FAST,
            [
                Message(role="system", content=system_prompt(_ROUTE_ROLE)),
                Message(role="user", content=question[:2_000]),
            ],
            _Classified,
            tags={**self._tags(state), "agent": "supervisor"},
        )
        update: dict[str, Any] = {
            "intent": classified.intent,
            "steps": [f"Supervisor: understood as {classified.intent.value}"],
        }
        if classified.intent == Intent.LETTER:
            update["letter_kind"] = classified.letter_kind or LetterKind.GRIEVANCE
        if classified.intent == Intent.OUT_OF_SCOPE:
            update["message"] = _OUT_OF_SCOPE
        return update

    def _after_route(self, state: GraphState) -> str:
        intent = state["intent"]
        if intent == Intent.OUT_OF_SCOPE:
            return END
        if intent == Intent.CHECKLIST:
            return "checklist"
        if not _has_inputs(intent, state["snapshot"]):
            return "blocked"
        return "answer" if intent == Intent.ASK else "terms"

    async def _blocked(self, state: GraphState) -> dict[str, Any]:
        needs = {
            Intent.ASK: "your policy document",
            Intent.AUDIT: "your policy document and the hospital bill or estimate",
            Intent.REVIEW: "your policy document and the insurer's letter",
            Intent.LETTER: "your policy document and the bill or the insurer's letter",
        }
        missing = needs.get(state["intent"], "your policy document")
        return {
            "message": f"Please upload {missing} first.",
            "steps": ["Supervisor: waiting for documents"],
        }

    async def _checklist(self, state: GraphState) -> dict[str, Any]:
        facts = state["facts"]
        checklist = build_checklist(
            claim_type=facts.claim_type or "reimbursement",
            accident=facts.is_accident,
            surgery=bool(facts.procedures),
        )
        return {"checklist": checklist, "steps": ["Checklist built from the claim type"]}

    async def _terms(self, state: GraphState) -> dict[str, Any]:
        snapshot = state["snapshot"]
        feedback = state.get("feedback", []) if state.get("retry") == "terms" else []
        reading = snapshot.reading if snapshot.reading and not feedback else None
        if reading is None:
            reading = await self._expert.read_terms(
                snapshot.case_id, snapshot.facts, feedback=feedback, tags=self._tags(state)
            )
        relation = reading.relation
        facts = snapshot.facts.model_copy(
            update={
                "treatment_is_pre_existing": relation.treatment_is_pre_existing,
                "specific_disease_matched": relation.specific_disease_matched,
                "sub_limit_matched": relation.sub_limit_matched,
            }
        )
        return {
            "reading": reading,
            "relation": relation,
            "facts": facts,
            "steps": [f"Policy Expert: read terms from {len(reading.clauses)} clauses"],
        }

    def _after_terms(self, state: GraphState) -> str:
        snapshot = state["snapshot"]
        if snapshot.bill is not None and state["intent"] in {Intent.AUDIT, Intent.LETTER}:
            return "audit"
        if snapshot.bill is not None and state["intent"] == Intent.REVIEW:
            return "audit"
        if snapshot.rejection is not None:
            return "review"
        return "letter"

    async def _audit(self, state: GraphState) -> dict[str, Any]:
        snapshot = state["snapshot"]
        if snapshot.bill is None:
            raise RuntimeError("audit node reached without a bill")
        outcome = await self._auditor.audit(
            snapshot.bill,
            state["reading"].terms,
            state["facts"],
            labelled=snapshot.labelled,
            tags=self._tags(state),
        )
        covered = outcome.coverage
        return {
            "audit": outcome,
            "steps": [
                f"Bill Auditor: {len(outcome.lines)} lines, Rs {covered.payable_total:,.2f} "
                f"payable of Rs {covered.billed_total:,.2f}"
            ],
        }

    def _after_audit(self, state: GraphState) -> str:
        if state["intent"] == Intent.AUDIT:
            return "check"
        if state["snapshot"].rejection is not None:
            return "review"
        return "letter"

    async def _review(self, state: GraphState) -> dict[str, Any]:
        snapshot = state["snapshot"]
        if snapshot.rejection is None:
            raise RuntimeError("review node reached without an insurer letter")
        feedback = state.get("feedback", []) if state.get("retry") == "review" else []
        audit = state.get("audit")
        review, clauses = await self._advocate.review(
            snapshot.case_id,
            snapshot.rejection,
            state["facts"],
            audit.coverage if audit else None,
            state["reading"].clauses,
            feedback=feedback,
            tags=self._tags(state),
        )
        return {
            "review": review,
            "review_clauses": clauses,
            "steps": [f"Claim Advocate: reviewed {len(review.reasons)} insurer reasons"],
        }

    def _after_review(self, state: GraphState) -> str:
        return "letter" if state["intent"] == Intent.LETTER else "check"

    async def _letter(self, state: GraphState) -> dict[str, Any]:
        snapshot = state["snapshot"]
        feedback = state.get("feedback", []) if state.get("retry") == "letter" else []
        audit = state.get("audit")
        coverage = audit.coverage if audit else None
        amounts = amount_table(coverage, snapshot.rejection)
        kind = state.get("letter_kind", LetterKind.GRIEVANCE)
        clauses = [*state.get("review_clauses", []), *state["reading"].clauses]
        letter = await self._advocate.draft(
            kind,
            parties=snapshot.parties,
            facts=state["facts"],
            clauses=clauses,
            amounts=amounts,
            review=state.get("review"),
            coverage=coverage,
            rejection=snapshot.rejection,
            feedback=feedback,
            tags=self._tags(state),
        )
        return {
            "letter": letter,
            "amounts": amounts,
            "steps": [f"Claim Advocate: drafted {kind.value} letter"],
        }

    async def _answer(self, state: GraphState) -> dict[str, Any]:
        snapshot = state["snapshot"]
        feedback = state.get("feedback", []) if state.get("retry") == "answer" else []
        turns = state.get("history", [])[-self._settings.chat_history_turns * 2 :]
        answer, clauses = await self._expert.answer(
            snapshot.case_id,
            state.get("question", ""),
            turns,
            state["facts"],
            feedback=feedback,
            tags=self._tags(state),
        )
        return {
            "answer": answer,
            "answer_clauses": clauses,
            "steps": [f"Policy Expert: answered from {len(clauses)} clauses"],
        }

    async def _check(self, state: GraphState) -> dict[str, Any]:
        intent = state["intent"]
        tags = self._tags(state)
        retry = ""
        if intent == Intent.ASK:
            report = await self._checker.answer(state["answer"], state["answer_clauses"], tags)
            retry = "answer"
        else:
            reading = state["reading"]
            report = self._checker.terms(reading.terms, reading.clauses)
            retry = "terms"
            if report.passed and intent == Intent.REVIEW:
                report = await self._checker.review(
                    state["review"], [*state["review_clauses"], *reading.clauses], tags
                )
                retry = "review"
            if report.passed and intent == Intent.LETTER:
                allowed = set(state["amounts"].values())
                if audit := state.get("audit"):
                    allowed |= audit.coverage.amounts()
                report = self._checker.letter(
                    state["letter"], [*state.get("review_clauses", []), *reading.clauses], allowed
                )
                retry = "letter"
        attempts = state.get("attempts", 0) + 1
        verdict = "passed" if report.passed else f"found {len(report.issues)} issues"
        return {
            "report": report,
            "attempts": attempts,
            "feedback": report.issues,
            "retry": retry,
            "steps": [f"Checker: {verdict}"],
        }

    def _after_check(self, state: GraphState) -> str:
        if state["report"].passed or state["attempts"] > self._settings.checker_max_retries:
            return END
        return state["retry"]
