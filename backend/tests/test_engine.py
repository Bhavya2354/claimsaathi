from datetime import date
from decimal import Decimal

import pytest

from app.domain import BillLine, LineCategory
from app.rules.engine import Reason, assess
from app.rules.terms import (
    CaseFacts,
    Copay,
    Deductible,
    LabelledLine,
    PolicyTerms,
    RoomLimit,
    SubLimit,
    WaitingKind,
    WaitingPeriod,
)


def line(
    description: str, amount: int, category: LineCategory, quantity: int | None = None
) -> LabelledLine:
    return LabelledLine(
        line=BillLine(
            description=description,
            amount=Decimal(amount),
            quantity=Decimal(quantity) if quantity is not None else None,
        ),
        category=category,
    )


@pytest.fixture
def facts() -> CaseFacts:
    return CaseFacts(
        policy_start_date=date(2023, 1, 1),
        admission_date=date(2026, 3, 1),
        discharge_date=date(2026, 3, 4),
    )


@pytest.fixture
def terms() -> PolicyTerms:
    return PolicyTerms(
        sum_insured=Decimal(500_000),
        room_rent=RoomLimit(basis="percent_of_sum_insured", value=Decimal(1), clause_ids=["r"]),
    )


def test_room_upgrade_scales_linked_charges_but_not_pharmacy(
    terms: PolicyTerms, facts: CaseFacts
) -> None:
    result = assess(
        terms,
        facts,
        [
            line("Single room", 24_000, LineCategory.ROOM, 3),
            line("Surgeon fee", 60_000, LineCategory.SURGERY),
            line("Pharmacy", 18_000, LineCategory.PHARMACY),
            line("CBC, LFT", 4_000, LineCategory.INVESTIGATIONS),
        ],
    )
    assert result.eligible_room_rent == Decimal("5000.00")
    assert result.charged_room_rent == Decimal("8000.00")
    assert result.proportion_ratio == Decimal("0.6250")
    room, surgeon, pharmacy, labs = result.lines
    assert room.payable == Decimal("15000.00")
    assert room.deductions[0].reason is Reason.ROOM_RENT_EXCESS
    assert room.deductions[0].clause_ids == ["r"]
    assert surgeon.payable == Decimal("37500.00")
    assert surgeon.deductions[0].reason is Reason.PROPORTIONATE_DEDUCTION
    assert pharmacy.payable == pharmacy.billed
    assert labs.payable == labs.billed
    assert result.payable_total == Decimal("74500.00")
    assert result.patient_pays == Decimal("31500.00")


def test_no_proportionate_cut_when_room_within_limit(terms: PolicyTerms, facts: CaseFacts) -> None:
    result = assess(
        terms,
        facts,
        [
            line("Twin sharing", 12_000, LineCategory.ROOM, 3),
            line("Surgeon fee", 60_000, LineCategory.SURGERY),
        ],
    )
    assert result.proportion_ratio == 1
    assert result.payable_total == Decimal("72000.00")


def test_policy_without_proportionate_clause(terms: PolicyTerms, facts: CaseFacts) -> None:
    terms = terms.model_copy(update={"proportionate_deduction": False})
    result = assess(
        terms,
        facts,
        [
            line("Single room", 24_000, LineCategory.ROOM, 3),
            line("Surgeon fee", 60_000, LineCategory.SURGERY),
        ],
    )
    assert result.lines[1].payable == Decimal("60000.00")
    assert result.lines[0].payable == Decimal("15000.00")


def test_non_payable_items_are_fully_deducted(terms: PolicyTerms, facts: CaseFacts) -> None:
    result = assess(terms, facts, [line("Gloves", 600, LineCategory.NON_PAYABLE)])
    assert result.lines[0].payable == 0
    assert result.lines[0].status == "not_covered"


def test_discount_rows_reduce_the_bill(terms: PolicyTerms, facts: CaseFacts) -> None:
    result = assess(
        terms,
        facts,
        [
            line("Pharmacy", 10_000, LineCategory.PHARMACY),
            line("Hospital discount", -1_000, LineCategory.OTHER_MEDICAL),
        ],
    )
    assert result.billed_total == Decimal("9000.00")
    assert result.payable_total == Decimal("9000.00")


def test_claim_level_order_sub_limit_then_deductible_then_copay(facts: CaseFacts) -> None:
    terms = PolicyTerms(
        sum_insured=Decimal(300_000),
        sub_limits=[SubLimit(treatment="Cataract", amount=Decimal(40_000), clause_ids=["s"])],
        deductible=Deductible(amount=Decimal(5_000)),
        copay=Copay(percent=Decimal(20), applies=True),
    )
    facts = facts.model_copy(update={"sub_limit_matched": "cataract"})
    result = assess(terms, facts, [line("Phaco surgery package", 65_000, LineCategory.SURGERY)])
    reasons = [d.reason for d in result.claim_deductions]
    assert reasons == [Reason.SUB_LIMIT, Reason.DEDUCTIBLE, Reason.CO_PAYMENT]
    assert [d.amount for d in result.claim_deductions] == [
        Decimal("25000.00"),
        Decimal("5000.00"),
        Decimal("7000.00"),
    ]
    assert result.payable_total == Decimal("28000.00")


def test_copay_that_does_not_apply_is_ignored(terms: PolicyTerms, facts: CaseFacts) -> None:
    terms = terms.model_copy(update={"copay": Copay(percent=Decimal(20), applies=False)})
    result = assess(terms, facts, [line("Pharmacy", 10_000, LineCategory.PHARMACY)])
    assert result.claim_deductions == []


def test_payout_capped_at_cover_left(terms: PolicyTerms, facts: CaseFacts) -> None:
    facts = facts.model_copy(update={"balance_sum_insured": Decimal(50_000)})
    result = assess(terms, facts, [line("Pharmacy", 80_000, LineCategory.PHARMACY)])
    assert result.claim_deductions[-1].reason is Reason.SUM_INSURED_EXCEEDED
    assert result.payable_total == Decimal("50000.00")


@pytest.mark.parametrize(
    ("kind", "update", "reason"),
    [
        (WaitingKind.INITIAL, {}, Reason.INITIAL_WAITING_PERIOD),
        (
            WaitingKind.PRE_EXISTING,
            {"treatment_is_pre_existing": True},
            Reason.PRE_EXISTING_WAITING_PERIOD,
        ),
        (
            WaitingKind.SPECIFIC_DISEASE,
            {"specific_disease_matched": "Hernia"},
            Reason.SPECIFIC_DISEASE_WAITING_PERIOD,
        ),
    ],
)
def test_waiting_periods_block_the_claim(
    terms: PolicyTerms,
    facts: CaseFacts,
    kind: WaitingKind,
    update: dict[str, object],
    reason: Reason,
) -> None:
    terms = terms.model_copy(
        update={"waiting_periods": [WaitingPeriod(kind=kind, days=1_200, clause_ids=["w"])]}
    )
    result = assess(
        terms, facts.model_copy(update=update), [line("Pharmacy", 10_000, LineCategory.PHARMACY)]
    )
    assert result.decision == "not_payable"
    assert result.claim_deductions[0].reason is reason
    assert result.claim_deductions[0].clause_ids == ["w"]
    assert result.patient_pays == Decimal("10000.00")


def test_accident_skips_initial_waiting_period(terms: PolicyTerms, facts: CaseFacts) -> None:
    terms = terms.model_copy(
        update={"waiting_periods": [WaitingPeriod(kind=WaitingKind.INITIAL, days=30)]}
    )
    facts = facts.model_copy(update={"policy_start_date": date(2026, 2, 20), "is_accident": True})
    result = assess(terms, facts, [line("Pharmacy", 10_000, LineCategory.PHARMACY)])
    assert result.decision == "payable"


def test_fixed_rupee_room_limit_and_icu(facts: CaseFacts) -> None:
    terms = PolicyTerms(
        sum_insured=Decimal(1_000_000),
        room_rent=RoomLimit(basis="amount_per_day", value=Decimal(4_000)),
        icu_rent=RoomLimit(basis="amount_per_day", value=Decimal(8_000)),
    )
    result = assess(
        terms,
        facts,
        [
            line("Ward", 9_000, LineCategory.ROOM, 2),
            line("ICU", 20_000, LineCategory.ICU, 2),
            line("Nursing", 3_000, LineCategory.NURSING),
        ],
    )
    ward, icu, nursing = result.lines
    assert ward.payable == Decimal("8000.00")
    assert icu.payable == Decimal("16000.00")
    assert nursing.payable == Decimal("2666.67")


def test_amounts_include_every_stated_figure(terms: PolicyTerms, facts: CaseFacts) -> None:
    result = assess(
        terms,
        facts,
        [
            line("Single room", 24_000, LineCategory.ROOM, 3),
            line("Surgeon fee", 60_000, LineCategory.SURGERY),
        ],
    )
    figures = result.amounts()
    assert Decimal("9000.00") in figures  # room excess
    assert Decimal("22500.00") in figures  # proportionate cut
    assert result.payable_total in figures
