"""Policy Expert: reads the policy's terms and answers questions, always with clause IDs.

Terms are read in three focused groups (limits, waiting periods, caps), each
from its own retrieved clauses. Small prompts stay inside free-tier token limits
and give the model less to confuse.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.agents.context import describe_facts, pack_clauses
from app.config import RetrievalSettings
from app.llm.prompting import system_prompt
from app.llm.router import ModelRouter
from app.llm.types import Message, Task
from app.policy.chunking import Clause
from app.policy.index import PolicyIndex
from app.rules.terms import (
    CaseFacts,
    Copay,
    Deductible,
    PolicyTerms,
    RoomLimit,
    SubLimit,
    WaitingPeriod,
)

_TERMS_ROLE = """You are a health insurance policy analyst in India. Read only the policy clauses
given and fill in the requested terms. Rules:
- Use only what the clauses state. If a term is not in them, leave it null or empty.
- Every term you fill must list the IDs of the clauses it came from in `clause_ids`, using the
  bracketed IDs shown (for example "a1b2c3-014"). Never invent an ID.
- Convert waiting periods to days (1 month = 30 days, 1 year = 365 days).
- Percentages are numbers, e.g. 1 for "1% of sum insured"."""

_GROUPS: dict[str, tuple[str, ...]] = {
    "limits": (
        "sum insured amount in the policy schedule",
        "room rent limit per day eligible room category",
        "ICU intensive care unit charges limit per day",
        "co-payment percentage applicable on claims",
        "deductible amount per claim or aggregate",
        "proportionate deduction associated medical expenses higher room category",
    ),
    "waiting": (
        "initial waiting period first 30 days except accident",
        "pre-existing disease waiting period months continuous coverage",
        "specific disease or procedure waiting period list of illnesses",
    ),
    "caps": (
        "sub-limit on specific treatment maximum amount payable",
        "cataract surgery limit per eye",
        "modern treatment methods limit",
    ),
}


class _Limits(BaseModel):
    sum_insured: float | None = None
    sum_insured_clause_ids: list[str] = Field(default_factory=list)
    room_rent: RoomLimit | None = None
    icu_rent: RoomLimit | None = None
    copay: Copay | None = Field(
        default=None, description="`applies` is true only if this case meets the condition."
    )
    deductible: Deductible | None = None
    proportionate_deduction: bool = True
    proportionate_clause_ids: list[str] = Field(default_factory=list)


class _Waiting(BaseModel):
    waiting_periods: list[WaitingPeriod] = Field(default_factory=list)


class _Caps(BaseModel):
    sub_limits: list[SubLimit] = Field(default_factory=list)


class CaseRelation(BaseModel):
    """How this admission relates to the policy's lists."""

    treatment_is_pre_existing: bool = Field(
        description="The treated illness existed before the policy started."
    )
    specific_disease_matched: str | None = Field(
        default=None, description="The specific-disease waiting list entry this treatment is in."
    )
    sub_limit_matched: str | None = Field(
        default=None, description="Exact `treatment` name of the sub-limit that applies."
    )
    reasoning: str = Field(description="One or two sentences on why.")


class PolicyAnswer(BaseModel):
    answer: str = Field(description="Plain-language answer for a patient's family.")
    clause_ids: list[str] = Field(default_factory=list, description="Clauses the answer rests on.")
    found_in_policy: bool = Field(description="False when the clauses do not answer the question.")


@dataclass(frozen=True, slots=True)
class _Reading:
    facts: CaseFacts
    feedback: tuple[str, ...]
    tags: dict[str, str]


class TermsReading(BaseModel):
    terms: PolicyTerms
    clauses: list[Clause]


_ANSWER_ROLE = """You explain Indian health insurance policies to patients and their families.
Answer only from the policy clauses given. Write short, plain sentences; explain any insurance
term you use. Cite the clause IDs your answer depends on. If the clauses do not answer the
question, say so and set found_in_policy to false; never guess. You do not give medical or
legal advice."""

_RELATE_ROLE = """You compare one hospital admission with a health insurance policy. Decide from
the clauses and case details whether the treated illness was pre-existing, whether it falls in a
specific-disease waiting list, and which sub-limit (if any) applies. Use the exact treatment
name from the sub-limit list. When the case details do not show it, answer false or null."""


class PolicyExpert:
    def __init__(
        self, router: ModelRouter, index: PolicyIndex, settings: RetrievalSettings
    ) -> None:
        self._router = router
        self._index = index
        self._settings = settings

    async def _retrieve(self, case_id: str, queries: Sequence[str]) -> list[Clause]:
        results = await asyncio.gather(
            *(
                self._index.search(case_id, q, top_k=self._settings.terms_clauses_per_query)
                for q in queries
            )
        )
        # Interleave so every query's best clause survives the context budget.
        ordered: list[Clause] = []
        for rank in range(self._settings.terms_clauses_per_query):
            ordered.extend(hits[rank].clause for hits in results if rank < len(hits))
        return ordered

    async def _read_group[T: BaseModel](
        self,
        case_id: str,
        group: str,
        schema: type[T],
        reading: _Reading,
    ) -> tuple[T, list[Clause]]:
        facts, feedback, tags = reading.facts, reading.feedback, reading.tags
        clauses = await self._retrieve(case_id, _GROUPS[group])
        context, used = pack_clauses(clauses, self._settings.context_max_chars)
        prompt = f"Policy clauses:\n{context}\n\nThis case:\n{describe_facts(facts)}"
        if feedback:
            prompt += "\n\nA reviewer rejected the last reading:\n- " + "\n- ".join(feedback)
        value = await self._router.structured(
            Task.REASONING,
            [
                Message(role="system", content=system_prompt(_TERMS_ROLE)),
                Message(role="user", content=prompt),
            ],
            schema,
            tags={**tags, "agent": "policy_expert", "group": group},
            use_cache=not feedback,
        )
        return value, used

    async def read_terms(
        self,
        case_id: str,
        facts: CaseFacts,
        *,
        feedback: Sequence[str] = (),
        tags: dict[str, str] | None = None,
    ) -> TermsReading:
        tags = tags or {}
        shared = _Reading(facts=facts, feedback=tuple(feedback), tags=tags)
        (limits, c1), (waiting, c2), (caps, c3) = await asyncio.gather(
            self._read_group(case_id, "limits", _Limits, shared),
            self._read_group(case_id, "waiting", _Waiting, shared),
            self._read_group(case_id, "caps", _Caps, shared),
        )
        terms = PolicyTerms(
            sum_insured=limits.sum_insured,
            clause_ids=limits.sum_insured_clause_ids,
            room_rent=limits.room_rent,
            icu_rent=limits.icu_rent,
            copay=limits.copay,
            deductible=limits.deductible,
            proportionate_deduction=limits.proportionate_deduction,
            proportionate_clause_ids=limits.proportionate_clause_ids,
            waiting_periods=waiting.waiting_periods,
            sub_limits=caps.sub_limits,
        )
        unique = {c.clause_id: c for c in [*c1, *c2, *c3]}
        return TermsReading(terms=terms, clauses=list(unique.values()))

    async def relate(
        self,
        terms: PolicyTerms,
        clauses: Sequence[Clause],
        facts: CaseFacts,
        tags: dict[str, str] | None = None,
    ) -> CaseRelation:
        sub_limits = ", ".join(s.treatment for s in terms.sub_limits) or "none"
        waits = ", ".join(c for w in terms.waiting_periods for c in w.conditions) or "none"
        lists = [f"Sub-limits: {sub_limits}", f"Specific-disease waits: {waits}"]
        context, _ = pack_clauses(clauses, self._settings.context_max_chars // 2)
        return await self._router.structured(
            Task.REASONING,
            [
                Message(role="system", content=system_prompt(_RELATE_ROLE)),
                Message(
                    role="user",
                    content=(
                        f"Case:\n{describe_facts(facts)}\n\n" + "\n".join(lists) + f"\n\n{context}"
                    ),
                ),
            ],
            CaseRelation,
            tags={**(tags or {}), "agent": "policy_expert", "step": "relate"},
        )

    async def answer(
        self,
        case_id: str,
        question: str,
        history: Sequence[Message],
        facts: CaseFacts,
        *,
        feedback: Sequence[str] = (),
        tags: dict[str, str] | None = None,
    ) -> tuple[PolicyAnswer, list[Clause]]:
        hits = await self._index.search(case_id, question)
        context, used = pack_clauses((h.clause for h in hits), self._settings.context_max_chars)
        prompt = f"Policy clauses:\n{context}\n\nCase:\n{describe_facts(facts)}\n\nQ: {question}"
        if feedback:
            prompt += "\n\nYour last answer was rejected:\n- " + "\n- ".join(feedback)
        value = await self._router.structured(
            Task.REASONING,
            [
                Message(role="system", content=system_prompt(_ANSWER_ROLE)),
                *history,
                Message(role="user", content=prompt),
            ],
            PolicyAnswer,
            tags={**(tags or {}), "agent": "policy_expert", "step": "answer"},
            use_cache=not feedback,
        )
        return value, used
