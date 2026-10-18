"""The documents a claim needs, chosen from the claim type. No model involved."""

import json
from functools import lru_cache
from importlib import resources
from typing import Literal

from pydantic import BaseModel


class ChecklistItem(BaseModel):
    document: str
    why: str
    group: str


class Checklist(BaseModel):
    items: list[ChecklistItem]
    source: str


@lru_cache
def _data() -> dict[str, object]:
    raw = resources.files("app.rules").joinpath("data/claim_documents.json").read_text("utf-8")
    data: dict[str, object] = json.loads(raw)
    return data


def build_checklist(
    *, claim_type: Literal["cashless", "reimbursement"], accident: bool, surgery: bool
) -> Checklist:
    data = _data()
    groups = ["common", claim_type]
    if accident:
        groups.append("accident")
    if surgery:
        groups.append("surgery")
    items: list[ChecklistItem] = []
    for group in groups:
        entries = data[group]
        if not isinstance(entries, list):
            raise TypeError(f"checklist group {group} is malformed")
        items.extend(ChecklistItem(group=group, **entry) for entry in entries)
    return Checklist(items=items, source=str(data["source"]))
