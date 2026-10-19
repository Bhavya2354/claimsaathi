"""Bill Auditor: labels each bill line, then hands the arithmetic to the rules engine.

Lines that match the IRDAI non-payable lists or an unambiguous billing keyword are
labelled without a model call. The rest go to a fast model in batches; a line the
model skips is kept as `other_medical` with a note, so nothing drops out of the totals.
"""

import asyncio
from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel, Field

from app.config import AgentSettings
from app.domain import Bill, BillLine, LineCategory
from app.llm.prompting import document_block, system_prompt
from app.llm.router import ModelRouter
from app.llm.types import Message, Task
from app.rules.catalogue import NonPayableCatalogue
from app.rules.engine import CoverageResult, assess
from app.rules.line_rules import LineRules
from app.rules.terms import CaseFacts, LabelledLine, PolicyTerms

_LABEL_ROLE = """You classify lines of Indian hospital bills for a health insurance audit.
Give every line exactly one category:
- room: room rent, bed or boarding charges outside ICU
- icu: ICU, ICCU, NICU, HDU bed charges
- nursing: nursing charges, ward care
- doctor_fees: consultations, visits, surgeon, anaesthetist, assistant surgeon, RMO
- surgery: OT charges, procedure charges, surgery packages
- investigations: lab tests, imaging, ECG, echo, pathology, radiology
- pharmacy: medicines, injections, IV fluids
- consumables: medical consumables used in care (syringes, cannula, catheters, sutures)
- implants: stents, intraocular lenses, plates, screws, mesh, pacemakers
- non_payable: non-medical items (toiletries, attendant food, admin or service charges)
- other_medical: other medical services (blood, physiotherapy, oxygen, dialysis, ambulance)
`reason` is a few words on why. Return one entry per line index given."""


class _Label(BaseModel):
    index: int
    category: LineCategory
    reason: str = Field(default="", max_length=200)


class _Labels(BaseModel):
    labels: list[_Label]


class AuditOutcome(BaseModel):
    lines: list[LabelledLine]
    coverage: CoverageResult
    warnings: list[str] = Field(default_factory=list)


class BillAuditor:
    def __init__(
        self,
        router: ModelRouter,
        catalogue: NonPayableCatalogue,
        rules: LineRules,
        settings: AgentSettings,
    ) -> None:
        self._router = router
        self._catalogue = catalogue
        self._rules = rules
        self._settings = settings

    async def label(self, lines: Sequence[BillLine], tags: dict[str, str]) -> list[LabelledLine]:
        labelled: dict[int, LabelledLine] = {}
        pending: list[tuple[int, BillLine]] = []
        for index, line in enumerate(lines):
            item = self._catalogue.match(line.description)
            ruled = self._rules.label(line.description)
            if item is not None and line.amount >= 0:
                labelled[index] = LabelledLine(
                    line=line,
                    category=LineCategory.NON_PAYABLE,
                    reason=f"IRDAI List {item.list_code}: {item.list_title}",
                    catalogue_item=item.item,
                )
            elif ruled is not None:
                labelled[index] = LabelledLine(
                    line=line, category=ruled, reason="Standard billing term."
                )
            else:
                pending.append((index, line))

        size = self._settings.audit_batch_size
        batches = [pending[i : i + size] for i in range(0, len(pending), size)]
        results = await asyncio.gather(*(self._label_batch(batch, tags) for batch in batches))
        for batch, labels in zip(batches, results, strict=True):
            by_index = {label.index: label for label in labels}
            for index, line in batch:
                label = by_index.get(index)
                labelled[index] = LabelledLine(
                    line=line,
                    category=label.category if label else LineCategory.OTHER_MEDICAL,
                    reason=label.reason if label else "Not classified; treated as medical.",
                )
        return [labelled[i] for i in range(len(lines))]

    async def _label_batch(
        self, batch: Sequence[tuple[int, BillLine]], tags: dict[str, str]
    ) -> list[_Label]:
        rows = "\n".join(
            f"{index} | {line.description} | {line.amount} | qty {line.quantity or '-'}"
            for index, line in batch
        )
        answer = await self._router.structured(
            Task.FAST,
            [
                Message(role="system", content=system_prompt(_LABEL_ROLE)),
                Message(role="user", content=document_block(rows, "bill lines")),
            ],
            _Labels,
            tags={**tags, "agent": "auditor", "lines": str(len(batch))},
        )
        wanted = {index for index, _ in batch}
        return [label for label in answer.labels if label.index in wanted]

    def reconcile(self, bill: Bill) -> list[str]:
        if bill.printed_total is None or bill.printed_total == 0:
            return []
        gap = abs(bill.lines_total - bill.printed_total)
        if gap <= abs(bill.printed_total) * Decimal(str(self._settings.bill_total_tolerance)):
            return []
        return [
            f"The lines read from the bill add up to Rs {bill.lines_total:,.2f}, but the bill "
            f"prints a total of Rs {bill.printed_total:,.2f}. Some lines may have been missed; "
            "check the bill pages."
        ]

    async def audit(
        self,
        bill: Bill,
        terms: PolicyTerms,
        facts: CaseFacts,
        *,
        labelled: Sequence[LabelledLine] | None = None,
        tags: dict[str, str] | None = None,
    ) -> AuditOutcome:
        lines = list(labelled) if labelled is not None else await self.label(bill.lines, tags or {})
        coverage = assess(terms, facts, lines)
        return AuditOutcome(lines=lines, coverage=coverage, warnings=self.reconcile(bill))
