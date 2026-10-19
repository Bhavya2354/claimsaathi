from decimal import Decimal

from app.documents.bill_table import parse_bill_table
from app.documents.reader import Page
from app.domain import LineCategory
from app.rules.line_rules import load_line_rules

SPACED = """Lotus Medical Centre
Date of Admission: 04/03/2026      Date of Discharge: 07/03/2026
S.No  Particulars                      Qty      Rate        Amount
1     Room Rent - Twin Sharing          3    4,000.00    12,000.00
2     Surgeon Fees                      1   45,000.00    45,000.00
3     Inj. Ceftriaxone 1g               6      350.00     2,100.00
4     Hospital Discount                                  (1,000.00)
      Grand Total                                         58,100.00
"""


def test_space_separated_bill_with_rate_and_discount() -> None:
    bill = parse_bill_table([Page(1, SPACED, "text_layer")])
    assert bill is not None
    assert [line.amount for line in bill.lines] == [
        Decimal("12000.00"),
        Decimal("45000.00"),
        Decimal("2100.00"),
        Decimal("-1000.00"),
    ]
    assert bill.lines[0].quantity == 3
    assert str(bill.admission_date) == "2026-03-04"
    assert bill.printed_total == Decimal("58100.00")


def test_bill_that_does_not_add_up_is_left_to_the_model() -> None:
    broken = SPACED.replace("58,100.00", "71,900.00")
    assert parse_bill_table([Page(1, broken, "text_layer")]) is None


def test_bill_without_a_printed_total_is_left_to_the_model() -> None:
    no_total = "\n".join(line for line in SPACED.splitlines() if "Grand Total" not in line)
    assert parse_bill_table([Page(1, no_total, "text_layer")]) is None


def test_keyword_rules_label_only_unambiguous_lines() -> None:
    rules = load_line_rules()
    assert rules.label("Inj. Pantoprazole 40mg") is LineCategory.PHARMACY
    assert rules.label("Foldable intraocular lens (IOL)") is LineCategory.IMPLANTS
    assert rules.label("Sutures and surgical consumables") is LineCategory.CONSUMABLES
    assert rules.label("Misc charges") is None
    assert rules.label("ICU nursing charges") is None  # two categories: the model decides
