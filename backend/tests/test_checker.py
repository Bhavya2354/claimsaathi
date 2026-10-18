from decimal import Decimal

from app.agents.advocate import LetterDraft, amount_table
from app.agents.checker import Checker, letter_amount_issues
from app.config import retrieval_settings
from app.domain import RejectionDecision, RejectionLetter
from app.policy.chunking import Clause
from app.rules.terms import PolicyTerms, RoomLimit, WaitingKind, WaitingPeriod

CLAUSES = [Clause("c-001", "4.1 Room Rent", 3, 3, "Room rent up to 1% of SI.")]


def checker() -> Checker:
    return Checker(router=None, settings=retrieval_settings())  # type: ignore[arg-type]


def test_terms_without_citations_are_flagged() -> None:
    terms = PolicyTerms(
        room_rent=RoomLimit(basis="percent_of_sum_insured", value=Decimal(1)),
        waiting_periods=[WaitingPeriod(kind=WaitingKind.INITIAL, days=30, clause_ids=["c-001"])],
    )
    report = checker().terms(terms, CLAUSES)
    assert not report.passed
    assert report.issues == ["The room rent limit has no clause citation."]


def test_invented_clause_ids_are_flagged() -> None:
    terms = PolicyTerms(
        room_rent=RoomLimit(
            basis="percent_of_sum_insured", value=Decimal(1), clause_ids=["c-001", "c-999"]
        )
    )
    report = checker().terms(terms, CLAUSES)
    assert report.issues == ["Cited clause IDs that were not provided: c-999."]


def test_cited_terms_pass() -> None:
    terms = PolicyTerms(
        room_rent=RoomLimit(basis="percent_of_sum_insured", value=Decimal(1), clause_ids=["c-001"])
    )
    assert checker().terms(terms, CLAUSES).passed


def test_letter_amounts_must_come_from_the_table() -> None:
    allowed = {Decimal("12500.00"), Decimal("40000.00")}
    body = "We request Rs 12,500.00 and note the Rs 40,000 cap. Also INR 99,999 was cut."
    assert letter_amount_issues(body, allowed) == [
        "The letter states Rs 99,999, which is not in the amount table."
    ]


def test_short_or_uncited_letters_fail() -> None:
    letter = LetterDraft(subject="Appeal", body="Please pay.", clause_ids=["c-404"])
    report = checker().letter(letter, CLAUSES, set())
    assert not report.passed
    assert len(report.issues) == 2


def test_amount_table_states_what_is_still_due() -> None:
    rejection = RejectionLetter(
        decision=RejectionDecision.PARTIALLY_APPROVED,
        amount_claimed=Decimal(100_000),
        amount_approved=Decimal(60_000),
    )
    table = amount_table(None, rejection)
    assert table == {
        "Amount claimed": Decimal(100_000),
        "Amount the insurer approved": Decimal(60_000),
    }
