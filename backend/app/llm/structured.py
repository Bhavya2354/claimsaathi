"""Turning free-text model answers into validated Pydantic objects."""

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.types import Message

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def schema_instruction(schema: type[BaseModel]) -> str:
    """Appended to the system prompt so models without native JSON mode still comply."""
    spec = json.dumps(schema.model_json_schema(), separators=(",", ":"))
    return (
        "Reply with one JSON object only, no prose and no code fences. "
        f"It must validate against this JSON Schema: {spec}"
    )


def extract_json(text: str) -> Any:
    """Parse the first JSON object in `text`, tolerating fences and leading prose."""
    fenced = _FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in the answer")
    return json.loads(candidate[start : end + 1])


def parse_as[T: BaseModel](schema: type[T], text: str) -> T:
    try:
        return schema.model_validate(extract_json(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(str(exc)[:800]) from exc


def repair_messages(messages: list[Message], answer: str, problem: str) -> list[Message]:
    """One follow-up turn asking the same model to fix its own output."""
    return [
        *messages,
        Message(role="assistant", content=answer),
        Message(
            role="user",
            content=(
                f"That answer was rejected: {problem}\n"
                "Send the corrected JSON object only, keeping every value you were sure of."
            ),
        ),
    ]
