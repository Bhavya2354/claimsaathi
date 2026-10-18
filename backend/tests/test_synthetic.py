from decimal import Decimal
from pathlib import Path

import pymupdf

from app.evals.synthetic import SCENARIOS, generate, load_manifest


def test_dataset_is_reproducible_and_answers_are_consistent(tmp_path: Path) -> None:
    first = load_manifest(generate(tmp_path / "a", cases=len(SCENARIOS), seed=3))
    second = load_manifest(generate(tmp_path / "b", cases=len(SCENARIOS), seed=3))
    assert [c.expected for c in first] == [c.expected for c in second]

    by_scenario = {case.scenario: case for case in first}
    assert by_scenario["pre_existing_wait"].expected["decision"] == "not_payable"
    assert by_scenario["cataract_sub_limit"].expected["claim_reasons"][0] == "sub_limit"
    assert by_scenario["senior_copay"].expected["claim_reasons"][-1] == "co_payment"
    assert by_scenario["unfair_rejection"].rejection["verdict"] == "not_supported"  # type: ignore[index]
    for case in first:
        billed = Decimal(case.expected["billed_total"])
        assert billed == sum(Decimal(line["amount"]) for line in case.lines)


def test_documents_are_readable_pdfs(tmp_path: Path) -> None:
    case = load_manifest(generate(tmp_path, cases=1, seed=1))[0]
    with pymupdf.open(tmp_path / case.case_id / case.documents["bill"]) as bill:
        text = "".join(page.get_text() for page in bill)
    assert "FINAL IN-PATIENT BILL" in text
    assert case.lines[0]["description"] in text
