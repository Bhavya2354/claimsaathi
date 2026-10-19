"""Policy terms and case facts: the inputs of the coverage calculation.

Every term carries the clause IDs it was read from, so each rupee the engine
deducts can be traced back to a page of the policy.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain import BillLine, LineCategory, Money


class _Term(BaseModel):
    model_config = ConfigDict(extra="ignore")

    clause_ids: list[str] = Field(default_factory=list, description="Clauses this was read from.")


class RoomLimit(_Term):
    basis: Literal["percent_of_sum_insured", "amount_per_day", "room_category", "no_limit"]
    value: Decimal | None = Field(
        default=None, description="Percent of sum insured per day, or rupees per day."
    )
    category: str | None = Field(default=None, description="Eligible room type, e.g. single AC.")


class Copay(_Term):
    percent: Decimal = Field(ge=0, le=100)
    applies: bool = Field(description="Whether this co-payment applies to this claim.")
    condition: str | None = Field(default=None, description="e.g. insured aged over 60.")


class Deductible(_Term):
    amount: Money = Field(ge=0)


class WaitingKind(StrEnum):
    INITIAL = "initial"
    PRE_EXISTING = "pre_existing"
    SPECIFIC_DISEASE = "specific_disease"


class WaitingPeriod(_Term):
    kind: WaitingKind
    days: int = Field(ge=0)
    conditions: list[str] = Field(default_factory=list, description="For specific-disease waits.")


class SubLimit(_Term):
    treatment: str = Field(description="Treatment or illness the cap applies to.")
    amount: Money | None = None
    percent_of_sum_insured: Decimal | None = None


class PolicyTerms(_Term):
    """`clause_ids` on the root holds the sum insured citation."""

    sum_insured: Money | None = None
    room_rent: RoomLimit | None = None
    icu_rent: RoomLimit | None = None
    copay: Copay | None = None
    deductible: Deductible | None = None
    waiting_periods: list[WaitingPeriod] = Field(default_factory=list)
    sub_limits: list[SubLimit] = Field(default_factory=list)
    proportionate_deduction: bool = Field(
        default=False,
        description="Whether a clause scales down linked charges for a costlier room.",
    )
    proportionate_clause_ids: list[str] = Field(default_factory=list)


class CaseFacts(BaseModel):
    """What happened in this admission: from the user's form and the discharge summary."""

    claim_type: Literal["cashless", "reimbursement"] | None = None
    patient_age: int | None = Field(default=None, ge=0, le=120)
    network_hospital: bool | None = None
    diagnosis: str | None = None
    procedures: list[str] = Field(default_factory=list)
    policy_start_date: date | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    sum_insured: Money | None = Field(default=None, description="Overrides the policy schedule.")
    balance_sum_insured: Money | None = None
    room_rent_per_day: Money | None = Field(default=None, description="Rent actually charged.")
    is_accident: bool = False
    pre_existing_conditions: list[str] = Field(default_factory=list)
    treatment_is_pre_existing: bool = False
    specific_disease_matched: str | None = Field(
        default=None, description="Specific-disease waiting list entry this treatment falls in."
    )
    sub_limit_matched: str | None = Field(
        default=None, description="`SubLimit.treatment` that applies to this treatment."
    )

    @property
    def stay_days(self) -> int | None:
        if self.admission_date and self.discharge_date:
            return max(1, (self.discharge_date - self.admission_date).days)
        return None


class LabelledLine(BaseModel):
    line: BillLine
    category: LineCategory
    reason: str | None = Field(default=None, description="Why the auditor chose the category.")
    catalogue_item: str | None = Field(
        default=None, description="Matched IRDAI non-payable list entry."
    )
    clause_ids: list[str] = Field(default_factory=list)
