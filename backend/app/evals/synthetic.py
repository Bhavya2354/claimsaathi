"""Synthetic claim cases with known answers.

Each case is a policy wording, a hospital bill, a discharge summary and sometimes
an insurer letter, rendered as PDFs, plus the ground truth: the true policy
terms, the true category of every bill line, and the settlement those imply
(computed by the tested rules engine). Scenarios plant the deductions that
trip people up: room upgrades, non-payable items, co-payment, sub-limits,
pre-existing disease waits, and insurer cuts the policy does not allow.

Generation is seeded, so a dataset can be rebuilt exactly.
"""

import json
import random
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf

from app.domain import BillLine, LineCategory
from app.rules.engine import CoverageResult, assess
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

SCENARIOS = (
    "room_upgrade",
    "within_limits",
    "senior_copay",
    "cataract_sub_limit",
    "pre_existing_wait",
    "unfair_rejection",
    "fair_rejection",
)
_UPGRADED = {"room_upgrade", "unfair_rejection", "senior_copay"}
_PRE_EXISTING = {"pre_existing_wait", "fair_rejection"}

_INSURERS = ("Arogya Mutual General", "Suraksha Health Insurance", "Navjeevan Health Co.")
_HOSPITALS = ("Sunrise Multispeciality Hospital", "City Care Hospital", "Lotus Medical Centre")
_NAMES = ("Ramesh Kumar Iyer", "Sunita Devi Sharma", "Arjun Mehta", "Farah Khan", "Prakash Rao")

Line = tuple[str, LineCategory, int, int | None]  # description, category, amount, quantity

_MEDICAL_LINES: dict[str, list[tuple[str, LineCategory, int, int]]] = {
    "general": [
        ("Nursing charges", LineCategory.NURSING, 1_500, 6_000),
        ("Consultant visit charges", LineCategory.DOCTOR_FEES, 3_000, 12_000),
        ("CBC, LFT, KFT, Serum electrolytes", LineCategory.INVESTIGATIONS, 2_500, 7_000),
        ("X-Ray chest PA view", LineCategory.INVESTIGATIONS, 600, 1_200),
        ("Inj. Ceftriaxone 1g", LineCategory.PHARMACY, 900, 3_200),
        ("IV Fluids (NS / RL)", LineCategory.PHARMACY, 700, 2_400),
        ("Tab. Pantoprazole 40 mg", LineCategory.PHARMACY, 150, 600),
        ("IV cannula and infusion set", LineCategory.CONSUMABLES, 400, 1_100),
        ("Physiotherapy session", LineCategory.OTHER_MEDICAL, 800, 2_500),
    ],
    "surgery": [
        ("Surgeon fees", LineCategory.DOCTOR_FEES, 25_000, 70_000),
        ("Anaesthetist fees", LineCategory.DOCTOR_FEES, 8_000, 20_000),
        ("Operation theatre charges", LineCategory.SURGERY, 15_000, 45_000),
        ("Sutures and surgical consumables", LineCategory.CONSUMABLES, 2_000, 6_000),
    ],
    "cataract": [
        ("Phacoemulsification surgery package", LineCategory.SURGERY, 35_000, 55_000),
        ("Foldable intraocular lens (IOL)", LineCategory.IMPLANTS, 12_000, 30_000),
        ("Ophthalmologist fees", LineCategory.DOCTOR_FEES, 5_000, 10_000),
    ],
}

_NON_PAYABLE_LINES = (
    "Surgical gloves",
    "Admission kit",
    "Attendant food charges",
    "Face mask N95",
    "Registration charges",
    "Documentation charges",
    "Diapers adult",
    "Thermometer",
)

_DEFINITIONS = (
    "Accident means a sudden, unforeseen and involuntary event caused by external, visible "
    "and violent means.",
    "Cashless facility means a facility extended by the insurer where the payments of the "
    "costs of treatment undergone by the insured person are made directly to the network "
    "provider to the extent pre-authorization is approved.",
    "Day care treatment means medical treatment or surgical procedure undertaken under general "
    "or local anaesthesia in a hospital in less than 24 hours because of technological "
    "advancement.",
    "Hospital means any institution established for in-patient care and day care treatment of "
    "illness and injuries, registered with the local authorities.",
    "Network provider means hospitals enlisted by the insurer, TPA or jointly to provide "
    "medical services to an insured by a cashless facility.",
    "Pre-existing disease means any condition, ailment, injury or disease that is diagnosed by "
    "a physician not more than 36 months prior to the date of commencement of the policy.",
    "Reasonable and customary charges means the charges for services or supplies which are "
    "the standard charges for the specific provider and consistent with the prevailing "
    "charges in the geographical area for identical or similar services.",
)


@dataclass
class SyntheticCase:
    case_id: str
    scenario: str
    facts: dict[str, Any]
    terms: dict[str, Any]
    lines: list[dict[str, Any]]
    expected: dict[str, Any]
    documents: dict[str, str]
    qa: list[list[dict[str, Any]]]
    rejection: dict[str, Any] | None = None


@dataclass(frozen=True)
class _People:
    insurer: str
    hospital: str
    holder: str
    policy_number: str
    bill_number: str
    uhid: str


def _money(value: int | Decimal) -> str:
    return f"{value:,.2f}"


def _write_pdf(path: Path, pages: list[str]) -> None:
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(
            pymupdf.Rect(48, 48, 547, 800), text, fontsize=9.5, fontname="helv", lineheight=1.3
        )
    doc.save(path)
    doc.close()


def _true_terms(rng: random.Random, scenario: str) -> PolicyTerms:
    room = (
        RoomLimit(basis="percent_of_sum_insured", value=Decimal(1))
        if rng.random() < 0.5
        else RoomLimit(basis="amount_per_day", value=Decimal(rng.choice((4_000, 5_000, 6_000))))
    )
    copay = (
        Copay(percent=Decimal(20), applies=True, condition="insured person aged above 60 years")
        if scenario == "senior_copay"
        else None
    )
    return PolicyTerms(
        sum_insured=Decimal(rng.choice((300_000, 500_000, 1_000_000))),
        room_rent=room,
        icu_rent=RoomLimit(basis="percent_of_sum_insured", value=Decimal(2)),
        copay=copay,
        deductible=Deductible(amount=Decimal(10_000)) if rng.random() < 0.2 else None,
        waiting_periods=[
            WaitingPeriod(kind=WaitingKind.INITIAL, days=30),
            WaitingPeriod(kind=WaitingKind.PRE_EXISTING, days=rng.choice((730, 1_095))),
            WaitingPeriod(
                kind=WaitingKind.SPECIFIC_DISEASE,
                days=730,
                conditions=["Cataract", "Hernia", "Knee replacement", "Kidney stones"],
            ),
        ],
        sub_limits=[SubLimit(treatment="Cataract", amount=Decimal(40_000))],
        proportionate_deduction=scenario != "unfair_rejection",
    )


def _ped_days(terms: PolicyTerms) -> int:
    return next(w.days for w in terms.waiting_periods if w.kind == WaitingKind.PRE_EXISTING)


def _limits_section(terms: PolicyTerms) -> str:
    room = terms.room_rent
    if room is not None and room.basis == "percent_of_sum_insured":
        room_text = f"up to {room.value}% of the sum insured per day"
    else:
        room_text = f"up to Rs {_money(room.value if room and room.value else 0)} per day"
    proportionate = (
        "4.2 Associated Medical Expenses\nIf the insured person is admitted in a room category "
        "with rent higher than the eligible limit, all associated medical expenses shall be "
        "payable in the proportion that the eligible room rent bears to the actual room rent, "
        "except cost of pharmacy and consumables, implants, diagnostics and ICU charges."
        if terms.proportionate_deduction
        else "4.2 Associated Medical Expenses\nAssociated medical expenses are payable in full "
        "and are not reduced because of the room category chosen."
    )
    copay = (
        "4.3 Co-payment\nA co-payment of 20% shall apply on every admissible claim where the "
        "insured person is aged above 60 years at the time of admission."
        if terms.copay
        else "4.3 Co-payment\nNo co-payment applies under this policy."
    )
    deductible = (
        f"4.4 Deductible\nAn aggregate deductible of Rs {_money(terms.deductible.amount)} "
        "applies per policy year before any claim becomes payable."
        if terms.deductible
        else "4.4 Deductible\nNo deductible applies under this policy."
    )
    return (
        f"4.1 Room Rent\nRoom, boarding and nursing expenses are payable {room_text}. "
        "Intensive care unit (ICU) charges are payable up to 2% of the sum insured per day.\n"
        f"{proportionate}\n{copay}\n{deductible}\n"
        "4.5 Sub-limits\nCataract treatment is payable up to Rs 40,000.00 per eye per policy "
        "year."
    )


def _policy_pages(terms: PolicyTerms, people: _People, start: date) -> list[str]:
    definitions = "\n".join(f"Def. {i} {text}" for i, text in enumerate(_DEFINITIONS, start=1))
    ped_months = _ped_days(terms) // 30
    return [
        f"{people.insurer}\nINDIVIDUAL HEALTH INSURANCE POLICY - POLICY WORDING\n\n"
        f"POLICY SCHEDULE\nPolicy holder: {people.holder}\nPolicy No: {people.policy_number}\n"
        f"Policy period start: {start.isoformat()}\n"
        f"Sum insured: Rs {_money(terms.sum_insured or 0)}\nPlan: Individual\n\n"
        "Section A. Preamble\nThis policy is a contract of insurance issued by the insurer to "
        "the proposer mentioned in the schedule. It is subject to the terms, conditions and "
        f"exclusions below.\n\nSection B. Definitions\n{definitions}",
        "Section C. Benefits covered under the policy\n"
        "3.1 In-patient Hospitalization\nThe company shall indemnify medical expenses incurred "
        "for hospitalization of the insured person during the policy period, up to the sum "
        "insured.\n3.2 Pre and Post Hospitalization\nMedical expenses incurred 30 days before "
        "admission and 60 days after discharge are covered.\n\nSection D. Limits\n"
        + _limits_section(terms),
        "Section E. Waiting Periods\n"
        "5.1 Initial Waiting Period (Code- Excl03)\nExpenses related to the treatment of any "
        "illness within 30 days from the first policy commencement date are excluded, except "
        "claims arising due to an accident.\n"
        "5.2 Pre-Existing Diseases (Code- Excl01)\nExpenses related to the treatment of a "
        "pre-existing disease and its direct complications are excluded until the expiry of "
        f"{ped_months} months of continuous coverage after the date of inception of the first "
        "policy.\n5.3 Specified Disease / Procedure Waiting Period (Code- Excl02)\nExpenses "
        "related to the treatment of the following are excluded until 24 months of continuous "
        "coverage: Cataract, Hernia, Knee replacement, Kidney stones.\n\n"
        "Section F. Exclusions\n6.1 Non-medical items listed by IRDAI (List I to IV of the "
        "standardization guidelines) such as gloves, admission kits, food for attendants and "
        "registration charges are not payable.\n6.2 Cosmetic or plastic surgery is excluded "
        "unless needed for reconstruction following an accident or cancer.\n\n"
        "Section G. Claim procedure\n7.1 Notify the company within 24 hours of an emergency "
        "admission and 48 hours before a planned admission. Submit reimbursement claims within "
        "30 days of discharge.\n7.2 Grievances may be sent to the Grievance Redressal Officer "
        "of the company. If not resolved in 15 days, the insured may approach the Insurance "
        "Ombudsman.",
    ]


def _eligible_rent(terms: PolicyTerms) -> int:
    room = terms.room_rent
    if room is None or room.value is None:
        return 5_000
    if room.basis == "percent_of_sum_insured":
        return int((terms.sum_insured or 0) * room.value / 100)
    return int(room.value)


def _bill_lines(rng: random.Random, scenario: str, terms: PolicyTerms, days: int) -> list[Line]:
    factor = rng.uniform(1.3, 1.8) if scenario in _UPGRADED else rng.uniform(0.6, 0.95)
    rent = int(_eligible_rent(terms) * factor)
    rent -= rent % 100
    lines: list[Line] = [
        ("Room rent - single private AC room", LineCategory.ROOM, rent * days, days)
    ]
    families = ["cataract"] if scenario == "cataract_sub_limit" else ["general", "surgery"]
    for family in families:
        for description, category, low, high in _MEDICAL_LINES[family]:
            if family == "general" and rng.random() < 0.25:
                continue
            amount = rng.randint(low, high)
            lines.append((description, category, amount - amount % 10, None))
    for description in rng.sample(_NON_PAYABLE_LINES, k=rng.randint(2, 4)):
        lines.append((description, LineCategory.NON_PAYABLE, rng.randint(150, 1_800), None))
    return lines


def _bill_pages(people: _People, lines: list[Line], admitted: date, discharged: date) -> list[str]:
    header = (
        f"{people.hospital}\nFINAL IN-PATIENT BILL\nBill No: {people.bill_number}\n"
        f"Patient Name: Mr./Ms. {people.holder}\nUHID: {people.uhid}\n"
        f"Date of admission: {admitted.isoformat()}   "
        f"Date of discharge: {discharged.isoformat()}\nRoom category: Single private AC\n\n"
        "Sr | Description | Qty | Amount (Rs)\n"
    )
    rows = [
        f"{i} | {desc} | {qty or 1} | {_money(amount)}"
        for i, (desc, _, amount, qty) in enumerate(lines, start=1)
    ]
    total = sum(amount for _, _, amount, _ in lines)
    footer = f"\nGross amount | {_money(total)}\nNet payable | {_money(total)}\n"
    split = len(rows) // 2 if len(rows) > 14 else len(rows)
    if split == len(rows):
        return [header + "\n".join(rows) + footer]
    return [
        header + "\n".join(rows[:split]),
        f"{people.hospital} - continued\n" + "\n".join(rows[split:]) + footer,
    ]


def _discharge(
    people: _People, scenario: str, admitted: date, discharged: date
) -> tuple[str, str, list[str]]:
    if scenario == "cataract_sub_limit":
        diagnosis, history = "Senile mature cataract, right eye", ["Hypertension for 2 years"]
        procedure = "Phacoemulsification with foldable IOL implantation, right eye"
    elif scenario in _PRE_EXISTING:
        diagnosis = "Diabetic foot ulcer with cellulitis, left foot"
        history = ["Type 2 diabetes mellitus for 6 years, on oral medication"]
        procedure = "Surgical debridement of left foot ulcer"
    else:
        diagnosis = "Acute calculous cholecystitis"
        history = ["No significant past history"]
        procedure = "Laparoscopic cholecystectomy"
    text = (
        f"{people.hospital}\nDISCHARGE SUMMARY\nPatient Name: {people.holder}\n"
        f"Date of admission: {admitted.isoformat()}\nDate of discharge: {discharged.isoformat()}"
        f"\n\nFinal diagnosis: {diagnosis}\nPast history: {'; '.join(history)}\n"
        f"Procedure done: {procedure}\n\nCourse in hospital: Patient was admitted with "
        "complaints related to the above diagnosis, evaluated and treated. The procedure was "
        "uneventful.\nCondition at discharge: Stable."
    )
    return text, diagnosis, history


def _rejection(
    folder: Path,
    rng: random.Random,
    scenario: str,
    people: _People,
    truth: CoverageResult,
) -> dict[str, Any]:
    claim_no = f"CL{rng.randint(10**7, 10**8 - 1)}"
    billed = truth.billed_total
    if scenario == "fair_rejection":
        reason = (
            "The claim is repudiated as the ailment, diabetic foot ulcer, is a complication of "
            "pre-existing type 2 diabetes and the policy is within the pre-existing disease "
            "waiting period under clause 5.2 (Code- Excl01)."
        )
        approved = Decimal(0)
        body = (
            f"{people.insurer}\nCLAIM REPUDIATION LETTER\nClaim No: {claim_no}\n"
            f"Insured: {people.holder}\n\nAmount claimed: Rs {_money(billed)}\n"
            f"Amount approved: Rs 0.00\n\nReason: {reason}\n\n"
            "You may represent to our Grievance Redressal Officer."
        )
        expected = {"verdict": "supported", "recommendation": "accept"}
    else:
        allowed_cut = billed - truth.payable_total
        unfair = Decimal(int(allowed_cut * Decimal(rng.uniform(0.25, 0.4))) + 2_000)
        approved = truth.payable_total - unfair
        reason = (
            f"Proportionate deduction of Rs {_money(unfair)} applied on associated medical "
            "expenses as the insured stayed in a room above the eligible category."
        )
        body = (
            f"{people.insurer}\nCLAIM SETTLEMENT LETTER - DEDUCTION DETAILS\n"
            f"Claim No: {claim_no}\nInsured: {people.holder}\n\n"
            f"Amount claimed: Rs {_money(billed)}\nAmount approved: Rs {_money(approved)}\n\n"
            "Deductions:\n1. Room rent above eligible limit and non-payable items as per "
            f"policy.\n2. {reason}\n"
        )
        expected = {"verdict": "not_supported", "recommendation": "appeal"}
    _write_pdf(folder / "rejection.pdf", [body])
    return {
        "claim_number": claim_no,
        "approved": str(approved),
        "key_reason": reason,
        **expected,
    }


def _qa(terms: PolicyTerms) -> list[list[dict[str, Any]]]:
    room = terms.room_rent
    if room is not None and room.basis == "percent_of_sum_insured":
        room_expect = ["1%"]
    else:
        amount = int(room.value) if room and room.value else 0
        room_expect = [f"{amount:,}", str(amount)]
    months = _ped_days(terms) // 30
    copay_expect = (
        ["20%"] if terms.copay else ["no co-payment", "not apply", "no copay", "does not"]
    )
    return [
        [
            {"question": "What is the room rent limit in my policy?", "expect_any": room_expect},
            {"question": "And what about ICU charges?", "expect_any": ["2%"]},
        ],
        [
            {
                "question": "How long must I wait before my pre-existing diabetes is covered?",
                "expect_any": [f"{months} months", f"{months // 12} years"],
            },
            {
                "question": (
                    "Explain in detail how the waiting periods work for cataract surgery and "
                    "what happens if I need it in my first year with this policy."
                ),
                "expect_any": ["24 months", "2 years", "two years"],
                "long_answer": True,
            },
        ],
        [
            {
                "question": "Is there any co-payment I have to pay myself?",
                "expect_any": copay_expect,
            },
            {
                "question": "Does the policy pay for cosmetic dental work?",
                "expect_any": ["cosmetic", "excluded", "not covered"],
            },
        ],
    ]


def build_case(index: int, scenario: str, seed: int, out_dir: Path) -> SyntheticCase:
    rng = random.Random(seed * 1_000 + index)
    case_id = f"case-{index:03d}-{scenario}"
    folder = out_dir / case_id
    folder.mkdir(parents=True, exist_ok=True)

    terms = _true_terms(rng, scenario)
    days = rng.randint(2, 5)
    admitted = date(2026, rng.randint(1, 8), rng.randint(1, 25))
    discharged = admitted + timedelta(days=days)
    ped = _ped_days(terms)
    if scenario in _PRE_EXISTING:
        start = admitted - timedelta(days=rng.randint(200, ped - 60))
    else:
        start = admitted - timedelta(days=rng.randint(ped + 30, ped + 900))
    people = _People(
        insurer=rng.choice(_INSURERS),
        hospital=rng.choice(_HOSPITALS),
        holder=rng.choice(_NAMES),
        policy_number=f"HL/{start.year}/{rng.randint(10**7, 10**8 - 1)}",
        bill_number=f"IP/{admitted.year}/{rng.randint(10**5, 10**6 - 1)}",
        uhid=f"SR{rng.randint(10**5, 10**6 - 1)}",
    )
    age = rng.randint(61, 75) if scenario == "senior_copay" else rng.randint(28, 58)
    lines = _bill_lines(rng, scenario, terms, days)
    summary, diagnosis, history = _discharge(people, scenario, admitted, discharged)

    _write_pdf(folder / "policy.pdf", _policy_pages(terms, people, start))
    _write_pdf(folder / "bill.pdf", _bill_pages(people, lines, admitted, discharged))
    _write_pdf(folder / "discharge.pdf", [summary])
    documents = {"policy": "policy.pdf", "bill": "bill.pdf", "discharge_summary": "discharge.pdf"}

    facts = CaseFacts(
        claim_type="reimbursement",
        patient_age=age,
        policy_start_date=start,
        admission_date=admitted,
        discharge_date=discharged,
        diagnosis=diagnosis,
        pre_existing_conditions=history if scenario in _PRE_EXISTING else [],
        treatment_is_pre_existing=scenario in _PRE_EXISTING,
        sub_limit_matched="Cataract" if scenario == "cataract_sub_limit" else None,
    )
    labelled = [
        LabelledLine(
            line=BillLine(
                description=desc, amount=Decimal(amount), quantity=Decimal(qty) if qty else None
            ),
            category=category,
        )
        for desc, category, amount, qty in lines
    ]
    truth = assess(terms, facts, labelled)

    rejection = None
    if scenario in {"unfair_rejection", "fair_rejection"}:
        rejection = _rejection(folder, rng, scenario, people, truth)
        documents["rejection_letter"] = "rejection.pdf"

    user_facts: dict[str, Any] = {
        "claim_type": "reimbursement",
        "patient_age": age,
        "policy_start_date": start.isoformat(),
    }
    if rng.random() < 0.5:
        user_facts["sum_insured"] = str(terms.sum_insured)

    return SyntheticCase(
        case_id=case_id,
        scenario=scenario,
        facts=user_facts,
        terms=terms.model_dump(mode="json"),
        lines=[
            {"description": d, "category": c.value, "amount": a, "quantity": q}
            for d, c, a, q in lines
        ],
        expected={
            "decision": truth.decision,
            "billed_total": str(truth.billed_total),
            "payable_total": str(truth.payable_total),
            "patient_pays": str(truth.patient_pays),
            "proportion_ratio": str(truth.proportion_ratio),
            "claim_reasons": [d.reason.value for d in truth.claim_deductions],
        },
        documents=documents,
        qa=_qa(terms),
        rejection=rejection,
    )


def generate(out_dir: Path, *, cases: int, seed: int) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    built = [build_case(i, SCENARIOS[i % len(SCENARIOS)], seed, out_dir) for i in range(cases)]
    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps([asdict(c) for c in built], indent=1), encoding="utf-8")
    return manifest


def load_manifest(path: Path) -> list[SyntheticCase]:
    return [SyntheticCase(**raw) for raw in json.loads(path.read_text(encoding="utf-8"))]
