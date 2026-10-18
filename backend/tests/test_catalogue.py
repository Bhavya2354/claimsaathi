import pytest

from app.rules.catalogue import load_catalogue


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Surgical Gloves (pair) x 10", "Gloves"),
        ("OXYGEN MASK ADULT", "Oxygen mask"),
        ("Admission Kit", "Admission kit"),
        ("Photocopies of records", "Photocopies charges"),
        ("ECG electrodes", "ECG electrodes"),
        ("Diapers adult L", "Diaper of any type"),
    ],
)
def test_matches_listed_items(description: str, expected: str) -> None:
    item = load_catalogue().match(description)
    assert item is not None
    assert item.item == expected


@pytest.mark.parametrize(
    "description",
    [
        "Cap. Omez 20mg",  # capsule, not a surgical cap
        "Throat swab culture",  # an investigation
        "Combiflam tab",  # not a comb
        "Pacing electrode lead",  # an implant
        "Inj. Ceftriaxone 1g",
    ],
)
def test_leaves_medical_items_alone(description: str) -> None:
    assert load_catalogue().match(description) is None
