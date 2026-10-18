"""Value types shared by the model client, the router and its callers."""

import base64
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from app.errors import ConfigurationError


class Task(StrEnum):
    """Which fallback chain a call uses."""

    REASONING = "reasoning"
    FAST = "fast"
    VISION = "vision"


@dataclass(frozen=True, slots=True)
class ModelRef:
    provider: str
    model: str

    @classmethod
    def parse(cls, entry: str) -> "ModelRef":
        """`provider:model`; model ids may themselves contain colons (`qwen3.5:9b`)."""
        provider, sep, model = entry.strip().partition(":")
        if not sep or not provider or not model:
            raise ConfigurationError(f"route entry must be provider:model, got {entry!r}")
        return cls(provider=provider, model=model)

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True, slots=True)
class Image:
    data: bytes
    mime_type: Literal["image/png", "image/jpeg", "image/webp"]

    def data_url(self) -> str:
        return f"data:{self.mime_type};base64,{base64.b64encode(self.data).decode()}"


@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str
    images: tuple[Image, ...] = ()


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    model: ModelRef
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached: bool = False


Outcome = Literal["ok", "rate_limited", "error", "invalid_output"]


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One attempt against one model, kept for tracing and model comparison."""

    task: Task
    model: ModelRef
    outcome: Outcome
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
