"""Matching bill lines against the IRDAI non-payable items lists.

Keywords match on word boundaries with an optional plural, so "Gloves" matches
"glove" but "Cap. Omez" (capsule) never matches "cap". A trailing `*` marks a
stem ("photocop*" for photocopy and photocopies).
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources


@dataclass(frozen=True, slots=True)
class CatalogueItem:
    item: str
    list_code: str
    list_title: str


@dataclass(frozen=True, slots=True)
class _Entry:
    item: CatalogueItem
    pattern: re.Pattern[str]


def _pattern(keyword: str) -> str:
    keyword = keyword.strip().lower()
    if keyword.endswith("*"):
        return rf"\b{re.escape(keyword[:-1])}"
    return rf"\b{re.escape(keyword)}(?:s|es)?\b"


class NonPayableCatalogue:
    def __init__(self, data: dict[str, object]) -> None:
        lists = data["lists"]
        items = data["items"]
        if not isinstance(lists, dict) or not isinstance(items, list):
            raise TypeError("non-payable catalogue is malformed")
        self.source = str(data["source"])
        self._entries: list[_Entry] = []
        for raw in items:
            item = CatalogueItem(
                item=raw["item"], list_code=raw["list"], list_title=lists[raw["list"]]
            )
            # Longer keywords first, so "oxygen mask" wins over "mask".
            for keyword in sorted(raw["keywords"], key=len, reverse=True):
                self._entries.append(_Entry(item, re.compile(_pattern(keyword))))
        self._entries.sort(key=lambda entry: -len(entry.pattern.pattern))

    def match(self, description: str) -> CatalogueItem | None:
        text = " ".join(description.lower().split())
        for entry in self._entries:
            if entry.pattern.search(text):
                return entry.item
        return None


@lru_cache
def load_catalogue() -> NonPayableCatalogue:
    raw = resources.files("app.rules").joinpath("data/non_payable_items.json").read_text("utf-8")
    return NonPayableCatalogue(json.loads(raw))
