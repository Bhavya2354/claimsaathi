"""Coverage calculation: what the insurer should pay for each bill line, and why.

Pure and deterministic. Models decide what a line *is* (its category) and which
terms the policy has; every rupee figure comes from here. The order follows how
Indian insurers settle a claim:

1. Waiting periods gate the whole claim.
2. Per line: non-payable items, room and ICU rent above the eligible limit, and
   proportionate deduction of linked charges when a costlier room was chosen.
3. Per claim: sub-limit cap, deductible, co-payment, then the sum insured left.
"""

import json
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from functools import lru_cache
from importlib import resources
from typing import Literal

from pydantic import BaseModel, Field, computed_field

from app.domain import LineCategory
from app.rules.terms import CaseFacts, LabelledLine, PolicyTerms, RoomLimit, WaitingKind

_ZERO = Decimal(0)
_HUNDRED = Decimal(100)


class Reason(StrEnum):
    NON_PAYABLE_ITEM = "non_payable_item"
    ROOM_RENT_EXCESS = "room_rent_excess"
    ICU_RENT_EXCESS = "icu_rent_excess"
    PROPORTIONATE_DEDUCTION = "proportionate_deduction"
    SUB_LIMIT = "sub_limit"
    DEDUCTIBLE = "deductible"
    CO_PAYMENT = "co_payment"
    SUM_INSURED_EXCEEDED = "sum_insured_exceeded"
    INITIAL_WAITING_PERIOD = "initial_waiting_period"
    PRE_EXISTING_WAITING_PERIOD = "pre_existing_waiting_period"
    SPECIFIC_DISEASE_WAITING_PERIOD = "specific_disease_waiting_period"


class Deduction(BaseModel):
    reason: Reason
    amount: Decimal
    explanation: str
    clause_ids: list[str] = Field(default_factory=list)


class LineDecision(BaseModel):
    index: int
    description: str
    category: LineCategory
    page: int | None
    billed: Decimal
    payable: Decimal
    deductions: list[Deduction] = Field(default_factory=list)
    catalogue_item: str | None = None
    note: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> Literal["covered", "partly_covered", "not_covered"]:
        if self.payable <= _ZERO < self.billed:
            return "not_covered"
        return "partly_covered" if self.payable < self.billed else "covered"


class CoverageResult(BaseModel):
    decision: Literal["payable", "not_payable"]
    lines: list[LineDecision]
    claim_deductions: list[Deduction]
    billed_total: Decimal
    line_payable_total: Decimal
    payable_total: Decimal
    patient_pays: Decimal
    eligible_room_rent: Decimal | None
    charged_room_rent: Decimal | None
    proportion_ratio: Decimal
    warnings: list[str] = Field(default_factory=list)

    def amounts(self) -> set[Decimal]:
        """Every rupee figure the result states; the checker verifies letters against these."""
        figures = {
            self.billed_total,
            self.line_payable_total,
            self.payable_total,
            self.patient_pays,
            self.billed_total - self.payable_total,
        }
        figures.update(d.amount for d in self.claim_deductions)
        for line in self.lines:
            figures.update((line.billed, line.payable, line.billed - line.payable))
            figures.update(d.amount for d in line.deductions)
        if self.eligible_room_rent is not None:
            figures.add(self.eligible_room_rent)
        if self.charged_room_rent is not None:
            figures.add(self.charged_room_rent)
        totals: dict[Reason, Decimal] = {}
        for line in self.lines:
            for d in line.deductions:
                totals[d.reason] = totals.get(d.reason, _ZERO) + d.amount
        figures.update(totals.values())
        return {_money(f) for f in figures}


class _Rules(BaseModel):
    proportionate_categories: frozenset[LineCategory]
    places: int


@lru_cache
def _rules() -> _Rules:
    raw = json.loads(
        resources.files("app.rules").joinpath("data/coverage_rules.json").read_text("utf-8")
    )
    return _Rules(
        proportionate_categories=frozenset(
            LineCategory(c) for c in raw["proportionate_deduction"]["applies_to"]
        ),
        places=int(raw["money_places"]),
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-_rules().places), rounding=ROUND_HALF_UP)


def _rupees(value: Decimal) -> str:
    return f"Rs {_money(value):,.2f}"


def _eligible_per_day(limit: RoomLimit | None, sum_insured: Decimal | None) -> Decimal | None:
    if limit is None or limit.value is None:
        return None
    if limit.basis == "amount_per_day":
        return _money(limit.value)
    if limit.basis == "percent_of_sum_insured" and sum_insured is not None:
        return _money(sum_insured * limit.value / _HUNDRED)
    return None


def _charged_per_day(lines: Sequence[LabelledLine], facts: CaseFacts) -> Decimal | None:
    if facts.room_rent_per_day is not None:
        return _money(facts.room_rent_per_day)
    room = [entry.line for entry in lines if entry.category == LineCategory.ROOM]
    if not room:
        return None
    total = sum((line.amount for line in room), _ZERO)
    days = sum((line.quantity or _ZERO for line in room), _ZERO) or Decimal(facts.stay_days or 1)
    return _money(total / days) if days > 0 else None


def _waiting_gate(terms: PolicyTerms, facts: CaseFacts) -> Deduction | None:
    if facts.policy_start_date is None or facts.admission_date is None:
        return None
    covered_days = (facts.admission_date - facts.policy_start_date).days
    for period in terms.waiting_periods:
        if covered_days >= period.days:
            continue
        if period.kind == WaitingKind.INITIAL and not facts.is_accident:
            reason, what = Reason.INITIAL_WAITING_PERIOD, "the initial waiting period"
        elif period.kind == WaitingKind.PRE_EXISTING and facts.treatment_is_pre_existing:
            reason, what = Reason.PRE_EXISTING_WAITING_PERIOD, "the pre-existing disease wait"
        elif period.kind == WaitingKind.SPECIFIC_DISEASE and facts.specific_disease_matched:
            reason = Reason.SPECIFIC_DISEASE_WAITING_PERIOD
            what = f"the waiting period for {facts.specific_disease_matched}"
        else:
            continue
        return Deduction(
            reason=reason,
            amount=_ZERO,
            explanation=(
                f"Admitted {covered_days} days after cover started, within {what} "
                f"of {period.days} days."
            ),
            clause_ids=period.clause_ids,
        )
    return None


def _line_decision(
    index: int,
    entry: LabelledLine,
    *,
    terms: PolicyTerms,
    facts: CaseFacts,
    eligible_room: Decimal | None,
    eligible_icu: Decimal | None,
    ratio: Decimal,
) -> LineDecision:
    line = entry.line
    billed = _money(line.amount)
    deductions: list[Deduction] = []
    payable = billed

    if billed < _ZERO:  # discount rows reduce the bill as printed
        pass
    elif entry.category == LineCategory.NON_PAYABLE:
        listed = f" ({entry.catalogue_item})" if entry.catalogue_item else ""
        deductions.append(
            Deduction(
                reason=Reason.NON_PAYABLE_ITEM,
                amount=billed,
                explanation=f"Non-medical item{listed}, not payable under IRDAI's list.",
                clause_ids=entry.clause_ids,
            )
        )
    elif entry.category in (LineCategory.ROOM, LineCategory.ICU):
        is_icu = entry.category == LineCategory.ICU
        eligible = eligible_icu if is_icu else eligible_room
        limit = terms.icu_rent if is_icu else terms.room_rent
        if eligible is not None:
            days = line.quantity or Decimal(facts.stay_days or 1)
            cap = _money(eligible * days)
            if billed > cap:
                deductions.append(
                    Deduction(
                        reason=Reason.ICU_RENT_EXCESS if is_icu else Reason.ROOM_RENT_EXCESS,
                        amount=billed - cap,
                        explanation=(
                            f"Eligible {'ICU' if is_icu else 'room'} rent is {_rupees(eligible)} "
                            f"a day for {days.normalize():f} days ({_rupees(cap)})."
                        ),
                        clause_ids=limit.clause_ids if limit else [],
                    )
                )
    elif entry.category in _rules().proportionate_categories and ratio < 1:
        cut = _money(billed * (1 - ratio))
        if cut > _ZERO:
            deductions.append(
                Deduction(
                    reason=Reason.PROPORTIONATE_DEDUCTION,
                    amount=cut,
                    explanation=(
                        f"A costlier room was chosen, so this charge is paid at "
                        f"{(ratio * _HUNDRED).quantize(Decimal('0.1'))}% (eligible rent / "
                        "charged rent)."
                    ),
                    clause_ids=[
                        *(terms.room_rent.clause_ids if terms.room_rent else []),
                        *terms.proportionate_clause_ids,
                    ],
                )
            )

    payable = billed - sum((d.amount for d in deductions), _ZERO)
    return LineDecision(
        index=index,
        description=line.description,
        category=entry.category,
        page=line.page,
        billed=billed,
        payable=_money(payable),
        deductions=deductions,
        catalogue_item=entry.catalogue_item,
        note=entry.reason,
    )


def _claim_deductions(
    admissible: Decimal, terms: PolicyTerms, facts: CaseFacts, sum_insured: Decimal | None
) -> tuple[Decimal, list[Deduction]]:
    deductions: list[Deduction] = []
    running = admissible

    if facts.sub_limit_matched:
        wanted = facts.sub_limit_matched.casefold()
        for sub in terms.sub_limits:
            if sub.treatment.casefold() != wanted:
                continue
            cap = sub.amount
            if cap is None and sub.percent_of_sum_insured is not None and sum_insured is not None:
                cap = sum_insured * sub.percent_of_sum_insured / _HUNDRED
            if cap is not None and running > cap:
                cap = _money(cap)
                deductions.append(
                    Deduction(
                        reason=Reason.SUB_LIMIT,
                        amount=running - cap,
                        explanation=f"{sub.treatment} is capped at {_rupees(cap)}.",
                        clause_ids=sub.clause_ids,
                    )
                )
                running = cap
            break

    if terms.deductible is not None and terms.deductible.amount > _ZERO and running > _ZERO:
        amount = min(running, _money(terms.deductible.amount))
        deductions.append(
            Deduction(
                reason=Reason.DEDUCTIBLE,
                amount=amount,
                explanation=f"The policy deductible of {_rupees(terms.deductible.amount)}.",
                clause_ids=terms.deductible.clause_ids,
            )
        )
        running -= amount

    if terms.copay is not None and terms.copay.applies and terms.copay.percent > 0:
        amount = _money(running * terms.copay.percent / _HUNDRED)
        if amount > _ZERO:
            condition = f" ({terms.copay.condition})" if terms.copay.condition else ""
            deductions.append(
                Deduction(
                    reason=Reason.CO_PAYMENT,
                    amount=amount,
                    explanation=f"Co-payment of {terms.copay.percent.normalize():f}%{condition}.",
                    clause_ids=terms.copay.clause_ids,
                )
            )
            running -= amount

    cover_left = facts.balance_sum_insured or sum_insured
    if cover_left is not None and running > cover_left:
        deductions.append(
            Deduction(
                reason=Reason.SUM_INSURED_EXCEEDED,
                amount=running - _money(cover_left),
                explanation=f"Only {_rupees(cover_left)} of cover is left this policy year.",
                clause_ids=terms.clause_ids,
            )
        )
        running = _money(cover_left)
    return _money(running), deductions


def _warnings(terms: PolicyTerms, facts: CaseFacts, sum_insured: Decimal | None) -> list[str]:
    notes: list[str] = []
    if terms.room_rent and terms.room_rent.basis == "percent_of_sum_insured" and not sum_insured:
        notes.append("Room rent is a share of the sum insured, but the sum insured is unknown.")
    if terms.room_rent and terms.room_rent.basis == "room_category":
        notes.append(
            f"Room rent is limited to a room type ({terms.room_rent.category or 'unspecified'}); "
            "check that the room used matches it."
        )
    if terms.waiting_periods and (facts.policy_start_date is None or facts.admission_date is None):
        notes.append("Waiting periods were not checked: policy start or admission date missing.")
    return notes


def assess(
    terms: PolicyTerms,
    facts: CaseFacts,
    lines: Sequence[LabelledLine],
) -> CoverageResult:
    sum_insured = facts.sum_insured or terms.sum_insured
    billed_total = _money(sum((entry.line.amount for entry in lines), _ZERO))
    eligible_room = _eligible_per_day(terms.room_rent, sum_insured)
    eligible_icu = _eligible_per_day(terms.icu_rent, sum_insured)
    charged_room = _charged_per_day(lines, facts)
    ratio = Decimal(1)
    if (
        terms.proportionate_deduction
        and eligible_room is not None
        and charged_room is not None
        and charged_room > eligible_room
    ):
        ratio = eligible_room / charged_room
    warnings = _warnings(terms, facts, sum_insured)

    gate = _waiting_gate(terms, facts)
    if gate is not None:
        gate = gate.model_copy(update={"amount": billed_total})
        decisions = [
            LineDecision(
                index=i,
                description=entry.line.description,
                category=entry.category,
                page=entry.line.page,
                billed=_money(entry.line.amount),
                payable=_ZERO,
                note=entry.reason,
            )
            for i, entry in enumerate(lines)
        ]
        return CoverageResult(
            decision="not_payable",
            lines=decisions,
            claim_deductions=[gate],
            billed_total=billed_total,
            line_payable_total=_ZERO,
            payable_total=_ZERO,
            patient_pays=billed_total,
            eligible_room_rent=eligible_room,
            charged_room_rent=charged_room,
            proportion_ratio=Decimal(1),
            warnings=warnings,
        )

    decisions = [
        _line_decision(
            i,
            entry,
            terms=terms,
            facts=facts,
            eligible_room=eligible_room,
            eligible_icu=eligible_icu,
            ratio=ratio,
        )
        for i, entry in enumerate(lines)
    ]
    line_payable = _money(sum((d.payable for d in decisions), _ZERO))
    payable, claim_deductions = _claim_deductions(line_payable, terms, facts, sum_insured)
    return CoverageResult(
        decision="payable" if payable > _ZERO else "not_payable",
        lines=decisions,
        claim_deductions=claim_deductions,
        billed_total=billed_total,
        line_payable_total=line_payable,
        payable_total=payable,
        patient_pays=billed_total - payable,
        eligible_room_rent=eligible_room,
        charged_room_rent=charged_room,
        proportion_ratio=ratio.quantize(Decimal("0.0001")),
        warnings=warnings,
    )
