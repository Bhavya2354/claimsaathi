"""Labelling common bill lines by keyword, so a model sees only the lines rules cannot settle.

A line gets a rule label only when exactly one category matches it. "Sutures and
surgical consumables" matches consumables alone; a line matching two categories,
or none, is left for the model.
"""

import json
import re
from functools import lru_cache
from importlib import resources

from app.domain import LineCategory


def _pattern(keyword: str) -> str:
    keyword = keyword.strip().lower()
    if keyword.endswith("*"):
        return rf"\b{re.escape(keyword[:-1])}"
    return rf"\b{re.escape(keyword)}\b"


class LineRules:
    def __init__(self, categories: dict[str, list[str]]) -> None:
        self._rules = [
            (LineCategory(name), re.compile("|".join(_pattern(k) for k in keywords)))
            for name, keywords in categories.items()
        ]

    def label(self, description: str) -> LineCategory | None:
        text = " ".join(description.lower().replace(".", " ").split())
        hits = {category for category, pattern in self._rules if pattern.search(text)}
        return hits.pop() if len(hits) == 1 else None


@lru_cache
def load_line_rules() -> LineRules:
    raw = resources.files("app.rules").joinpath("data/line_categories.json").read_text("utf-8")
    return LineRules(json.loads(raw)["categories"])
