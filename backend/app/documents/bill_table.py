"""Reading bill tables without a model, when the rows prove themselves.

Hospital billing systems print one charge per row ending in an amount, with
columns split by pipes or runs of spaces. Rows are parsed directly, and the
result is used only if the rows add up to the total printed on the bill. A bill
that does not reconcile goes to the model, so a parsing slip can never pass as
a smaller bill.
"""

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation

from app.documents.reader import Page
from app.domain import Bill, BillLine

_AMOUNT = r"-?\(?[\d,]+\.\d{2}\)?"
_ROW = re.compile(
    rf"""^\s*(?:\d{{1,3}}\s*(?:\||\.|\)|\s)\s*)?      # serial number
        (?P<desc>[A-Za-z][^|\n]{{2,120}}?)              # description
        (?:\s*(?:\||\s{{2,}})\s*(?P<qty>\d{{1,4}}(?:\.\d{{1,2}})?))?   # quantity
        (?:\s*(?:\||\s{{2,}})\s*{_AMOUNT})?             # rate, if printed
        \s*(?:\||\s{{2,}})\s*(?:rs\.?\s*)?(?P<amount>{_AMOUNT})\s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_TOTAL = re.compile(
    rf"(?:net\s+payable|net\s+amount|grand\s+total|total\s+amount|bill\s+amount)\b"
    rf"[^\n\d-]{{0,80}}(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)
_NOT_A_CHARGE = re.compile(
    r"\b(total|sub-?total|gross|net payable|advance|deposit|paid|balance|amount due|"
    r"round(?:ing)? off|cgst|sgst|igst)\b",
    re.IGNORECASE,
)
_DATE = r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})"
_ADMITTED = re.compile(
    rf"(?:date of admission|admission date|admitted on|doa)\W{{0,5}}{_DATE}", re.I
)
_DISCHARGED = re.compile(
    rf"(?:date of discharge|discharge date|discharged on|dod)\W{{0,5}}{_DATE}", re.I
)
_MIN_ROWS = 3
_TOLERANCE = Decimal("1.00")


def _money(text: str) -> Decimal | None:
    negative = text.startswith(("-", "("))
    try:
        value = Decimal(text.strip("()-").replace(",", ""))
    except InvalidOperation:
        return None
    return -value if negative else value


def _date(match: re.Match[str] | None) -> date | None:
    if match is None:
        return None
    raw = match.group(1)
    try:
        if "-" in raw and len(raw.split("-")[0]) == 4:  # noqa: PLR2004 - ISO year first
            return date.fromisoformat(raw)
        day, month, year = re.split(r"[/-]", raw)
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def parse_bill_table(pages: Sequence[Page]) -> Bill | None:
    """A bill whose rows add up to its printed total, or None to let a model read it."""
    lines: list[BillLine] = []
    printed_total: Decimal | None = None
    for page in pages:
        for raw in page.text.splitlines():
            if total := _TOTAL.search(raw):
                printed_total = _money(total.group("amount")) or printed_total
                continue
            row = _ROW.match(raw)
            if row is None or _NOT_A_CHARGE.search(row.group("desc")):
                continue
            amount = _money(row.group("amount"))
            if amount is None:
                continue
            quantity = Decimal(row.group("qty")) if row.group("qty") else None
            lines.append(
                BillLine(
                    description=row.group("desc").strip(" -:"),
                    amount=amount,
                    quantity=quantity,
                    page=page.number,
                )
            )
    if len(lines) < _MIN_ROWS or printed_total is None:
        return None
    if abs(sum((line.amount for line in lines), Decimal(0)) - printed_total) > _TOLERANCE:
        return None
    text = "\n".join(page.text for page in pages)
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), None)
    return Bill(
        hospital_name=first_line,
        admission_date=_date(_ADMITTED.search(text)),
        discharge_date=_date(_DISCHARGED.search(text)),
        printed_total=printed_total,
        lines=lines,
    )
