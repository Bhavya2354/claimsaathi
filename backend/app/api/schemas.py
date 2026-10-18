"""Request bodies. Response models live in `app.services.views`."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agents.advocate import LetterKind
from app.domain import LineCategory


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FactsUpdate(_Body):
    """Details the user enters; omitted fields are left as they are."""

    claim_type: Literal["cashless", "reimbursement"] | None = None
    patient_age: int | None = Field(default=None, ge=0, le=120)
    network_hospital: bool | None = None
    diagnosis: str | None = Field(default=None, max_length=300)
    policy_start_date: date | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    sum_insured: Decimal | None = Field(default=None, ge=0, le=100_000_000)
    balance_sum_insured: Decimal | None = Field(default=None, ge=0, le=100_000_000)
    room_rent_per_day: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    is_accident: bool | None = None


class EstimateLine(_Body):
    description: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(ge=0, le=100_000_000)
    category: LineCategory
    quantity: Decimal | None = Field(default=None, gt=0, le=365)


class EstimateRequest(_Body):
    """A planned admission. Without lines, the uploaded cost estimate is used instead."""

    lines: list[EstimateLine] = Field(default_factory=list, max_length=100)


class LetterRequest(_Body):
    kind: LetterKind = LetterKind.GRIEVANCE


class LetterEdit(_Body):
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)


class ChatRequest(_Body):
    message: str = Field(min_length=1, max_length=2_000)


class FeedbackRequest(_Body):
    target_type: Literal["analysis", "letter", "message"]
    target_id: str = Field(min_length=36, max_length=36)
    helpful: bool
    comment: str | None = Field(default=None, max_length=1_000)
