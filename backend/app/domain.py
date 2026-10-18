"""Domain records shared by extraction, the rules engine, the agents and the API.

Money is `Decimal` everywhere. Model output arrives as numbers or strings such as
"Rs. 1,284.00", so `Money` normalises both before validation.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

_CURRENCY = re.compile(r"(?i)(rs\.?|inr|₹|/-)")


def _to_decimal(value: Any) -> Any:
    if value is None or isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = _CURRENCY.sub("", value).replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return value
    return value


Money = Annotated[Decimal, BeforeValidator(_to_decimal)]


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class DocumentKind(StrEnum):
    POLICY = "policy"
    BILL = "bill"
    ESTIMATE = "estimate"
    DISCHARGE_SUMMARY = "discharge_summary"
    REJECTION_LETTER = "rejection_letter"
    OTHER = "other"


class LineCategory(StrEnum):
    """How a bill line is treated under the policy. Assigned by the auditor."""

    ROOM = "room"
    ICU = "icu"
    NURSING = "nursing"
    DOCTOR_FEES = "doctor_fees"
    SURGERY = "surgery"
    INVESTIGATIONS = "investigations"
    PHARMACY = "pharmacy"
    CONSUMABLES = "consumables"
    IMPLANTS = "implants"
    NON_PAYABLE = "non_payable"
    OTHER_MEDICAL = "other_medical"


class BillLine(_Record):
    description: str = Field(min_length=1)
    amount: Money = Field(description="Net amount charged for this line, in rupees.")
    quantity: Decimal | None = None
    service_date: date | None = None
    page: int | None = Field(default=None, description="Page number the line was read from.")


class Bill(_Record):
    hospital_name: str | None = None
    bill_number: str | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    room_category: str | None = Field(default=None, description="e.g. single private, twin, ward.")
    room_rent_per_day: Money | None = None
    printed_total: Money | None = Field(default=None, description="Net payable as printed.")
    lines: list[BillLine] = Field(default_factory=list)

    @property
    def lines_total(self) -> Decimal:
        return sum((line.amount for line in self.lines), Decimal(0))


class DischargeSummary(_Record):
    diagnosis: str | None = None
    procedures: list[str] = Field(default_factory=list)
    admission_date: date | None = None
    discharge_date: date | None = None
    past_history: list[str] = Field(
        default_factory=list, description="Conditions noted as existing before admission."
    )
    is_accident: bool | None = None


class InsurerReason(_Record):
    text: str = Field(min_length=1, description="The insurer's reason, as written.")
    clause_reference: str | None = Field(default=None, description="Clause the insurer cites.")
    amount: Money | None = Field(default=None, description="Amount cut for this reason, if given.")


class RejectionDecision(StrEnum):
    REJECTED = "rejected"
    PARTIALLY_APPROVED = "partially_approved"
    QUERY = "query"


class RejectionLetter(_Record):
    insurer_name: str | None = None
    claim_number: str | None = None
    decision: RejectionDecision
    amount_claimed: Money | None = None
    amount_approved: Money | None = None
    letter_date: date | None = None
    reasons: list[InsurerReason] = Field(default_factory=list)
