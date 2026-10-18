from functools import cache

from app.config import privacy_settings
from app.documents.privacy import PiiMasker

BILL = """Patient Name: Mr. Ramesh Kumar Sharma   Age: 54
UHID: MH20231187  Policy No: 1201/2245/889901
Mobile: +91 98450 12345  Aadhaar 2345 6789 0124  PAN ABCPS1234K
1 | Dolo 650 Tablet | 10 | 32.00
2 | Augmentin Duo | 2 | 410.00"""


@cache
def masker() -> PiiMasker:
    return PiiMasker(privacy_settings())


def test_identifiers_and_names_are_masked() -> None:
    vault: dict[str, str] = {}
    masked = masker().mask(BILL, vault)
    for secret in ("Ramesh", "MH20231187", "1201/2245/889901", "98450", "2345 6789", "ABCPS1234K"):
        assert secret not in masked
    assert "[PERSON_1]" in masked
    assert "[POLICY_NUMBER_1]" in masked


def test_medicine_names_survive() -> None:
    masked = masker().mask(BILL, {})
    assert "Dolo 650 Tablet" in masked
    assert "Augmentin Duo" in masked


def test_tokens_are_stable_across_documents() -> None:
    vault: dict[str, str] = {}
    first = masker().mask("Patient Name: Mr. Ramesh Kumar Sharma", vault)
    second = masker().mask("Insured: Mr. Ramesh Kumar Sharma", vault)
    assert first.split()[-1] == second.split()[-1] == "[PERSON_1]"


def test_round_trip_and_remask() -> None:
    vault: dict[str, str] = {}
    masked = masker().mask(BILL, vault)
    clear = PiiMasker.unmask(masked, vault)
    assert "Ramesh Kumar Sharma" in clear
    assert PiiMasker.remask(clear, vault) == masked


def test_vault_is_encrypted_at_rest() -> None:
    vault = {"[PERSON_1]": "Ramesh Kumar Sharma"}
    sealed = masker().seal(vault)
    assert b"Ramesh" not in sealed
    assert masker().unseal(sealed) == vault
    assert masker().unseal(None) == {}


def test_unmask_tree_handles_nested_data() -> None:
    vault = {"[PERSON_1]": "Asha"}
    data = {"a": ["Dear [PERSON_1]", {"b": "[PERSON_1]"}], "n": 3}
    assert PiiMasker.unmask_tree(data, vault) == {"a": ["Dear Asha", {"b": "Asha"}], "n": 3}
