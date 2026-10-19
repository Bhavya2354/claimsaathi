"""Building model context within free-tier token budgets.

Clauses are packed best-first until the character budget is spent, and each is
labelled with its ID so answers can cite it and the checker can verify the cite.
"""

from collections.abc import Iterable

from app.llm.prompting import document_block
from app.policy.chunking import Clause
from app.rules.terms import CaseFacts


def pack_clauses(clauses: Iterable[Clause], max_chars: int) -> tuple[str, list[Clause]]:
    """Clause blocks in order until `max_chars`; returns the text and the clauses it holds."""
    blocks: list[str] = []
    used: list[Clause] = []
    seen: set[str] = set()
    spent = 0
    for clause in clauses:
        if clause.clause_id in seen:
            continue
        block = document_block(clause.text, f"clause {clause.clause_id}")
        block = block.replace(">\n", f">\n[{clause.clause_id}] {clause.citation}\n", 1)
        if used and spent + len(block) > max_chars:
            break
        blocks.append(block)
        used.append(clause)
        seen.add(clause.clause_id)
        spent += len(block)
    return "\n\n".join(blocks), used


def describe_facts(facts: CaseFacts) -> str:
    rows = {
        "Claim type": facts.claim_type,
        "Patient age": facts.patient_age,
        "Network hospital": facts.network_hospital,
        "Diagnosis": facts.diagnosis,
        "Procedures": ", ".join(facts.procedures) or None,
        "Policy start date": facts.policy_start_date,
        "Admission date": facts.admission_date,
        "Discharge date": facts.discharge_date,
        "Sum insured": facts.sum_insured,
        "Cover left this year": facts.balance_sum_insured,
        "Room rent charged per day": facts.room_rent_per_day,
        "Accident": facts.is_accident,
        "Conditions before admission": ", ".join(facts.pre_existing_conditions) or None,
        "Days of cover at admission": (
            (facts.admission_date - facts.policy_start_date).days
            if facts.admission_date and facts.policy_start_date
            else None
        ),
    }
    lines = [f"- {key}: {value}" for key, value in rows.items() if value not in (None, "")]
    return "\n".join(lines) or "- No case details given."
