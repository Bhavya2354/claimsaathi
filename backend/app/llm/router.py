"""Failover across free model providers.

Each task has an ordered chain of `provider:model` entries. A call walks the chain:
transient errors are retried with jittered backoff, rate limits and repeated
failures put that model on a cooldown (circuit breaker), and an answer that fails
validation gets one repair turn before the next model is tried.
"""

import asyncio
import dataclasses
import hashlib
import logging
import random
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx
from pydantic import BaseModel

from app.config import ModelCallSettings, ProviderSettings, RouteSettings
from app.errors import ModelUnavailableError, StructuredOutputError
from app.llm.client import ChatClient, Endpoint, ProviderError, RateLimitedError
from app.llm.structured import parse_as, repair_messages, schema_instruction
from app.llm.types import CallRecord, Completion, Message, ModelRef, Outcome, Task

log = logging.getLogger(__name__)

_INVALID = "invalid output"


class CallRecorder(Protocol):
    async def __call__(self, record: CallRecord) -> None: ...


async def _discard(record: CallRecord) -> None:
    return None


@dataclass(frozen=True, slots=True)
class _Request[T]:
    task: Task
    messages: list[Message]
    accept: Callable[[str], T]
    json_mode: bool
    tags: dict[str, str]


@dataclass(slots=True)
class _Breaker:
    failures: int = 0
    open_until: float = 0.0


class _ResponseCache:
    """In-process LRU with TTL. Answers are model-agnostic, so any model's answer is reused."""

    def __init__(self, ttl_seconds: int, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._items: OrderedDict[str, tuple[float, Completion]] = OrderedDict()

    def get(self, key: str) -> Completion | None:
        item = self._items.get(key)
        if item is None:
            return None
        stored_at, completion = item
        if time.monotonic() - stored_at > self._ttl:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return completion

    def put(self, key: str, completion: Completion) -> None:
        if self._max == 0 or self._ttl == 0:
            return
        self._items[key] = (time.monotonic(), completion)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)


def _cache_key(task: Task, messages: Sequence[Message], schema: str | None) -> str:
    digest = hashlib.sha256(f"{task}|{schema}".encode())
    for message in messages:
        digest.update(message.role.encode())
        digest.update(message.content.encode())
        for image in message.images:
            digest.update(hashlib.sha256(image.data).digest())
    return digest.hexdigest()


class ModelRouter:
    def __init__(
        self,
        client: ChatClient,
        routes: dict[Task, list[ModelRef]],
        settings: ModelCallSettings,
        recorder: CallRecorder = _discard,
    ) -> None:
        self._client = client
        self._settings = settings
        self._recorder = recorder
        self._routes = {
            task: [ref for ref in chain if client.has_provider(ref.provider)]
            for task, chain in routes.items()
        }
        self._breakers: dict[ModelRef, _Breaker] = {}
        self._slots: dict[str, asyncio.Semaphore] = {}
        self._cache = _ResponseCache(settings.llm_cache_ttl_seconds, settings.llm_cache_max_entries)

    def chain(self, task: Task) -> list[ModelRef]:
        return list(self._routes.get(task, []))

    def summary(self) -> dict[str, list[str]]:
        return {task.value: [str(m) for m in self.chain(task)] for task in Task}

    async def complete(
        self,
        task: Task,
        messages: list[Message],
        *,
        tags: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> Completion:
        request = _Request(task, messages, str, json_mode=False, tags=tags or {})
        completion, _ = await self._run(request, use_cache=use_cache, schema=None)
        return completion

    async def structured[T: BaseModel](
        self,
        task: Task,
        messages: list[Message],
        schema: type[T],
        *,
        tags: dict[str, str] | None = None,
        use_cache: bool = True,
    ) -> T:
        system, *rest = messages
        guided = [
            Message(role="system", content=f"{system.content}\n\n{schema_instruction(schema)}"),
            *rest,
        ]
        request = _Request(
            task, guided, lambda text: parse_as(schema, text), json_mode=True, tags=tags or {}
        )
        _, value = await self._run(request, use_cache=use_cache, schema=schema)
        return value

    async def _run[T](
        self, request: _Request[T], *, use_cache: bool, schema: type[BaseModel] | None
    ) -> tuple[Completion, T]:
        task = request.task
        key = _cache_key(task, request.messages, schema.__name__ if schema else None)
        if use_cache and (hit := self._cache.get(key)) is not None:
            return dataclasses.replace(hit, cached=True), request.accept(hit.text)

        chain = self._routes.get(task, [])
        if not chain:
            raise ModelUnavailableError(f"no enabled provider on the {task} route")
        reasons: list[str] = []
        for model in chain:
            breaker = self._breakers.setdefault(model, _Breaker())
            if time.monotonic() < breaker.open_until:
                reasons.append(f"{model}: cooling down")
                continue
            result = await self._try_model(model, request)
            if isinstance(result, str):
                reasons.append(result)
                continue
            if use_cache:
                self._cache.put(key, result[0])
            return result

        if schema is not None and any(r.endswith(_INVALID) for r in reasons):
            raise StructuredOutputError(f"{task}: no model produced valid {schema.__name__}")
        raise ModelUnavailableError(f"{task}: every model failed ({'; '.join(reasons)})")

    async def _try_model[T](
        self, model: ModelRef, request: _Request[T]
    ) -> tuple[Completion, T] | str:
        """The accepted answer, or a short reason this model was given up on."""
        turn = request.messages
        for _ in range(2):  # first answer, then one repair turn
            completion = await self._call_with_retries(model, request, turn)
            if isinstance(completion, str):
                return completion
            try:
                value = request.accept(completion.text)
            except ValueError as exc:
                await self._record(model, request, "invalid_output", completion, str(exc))
                turn = repair_messages(request.messages, completion.text, str(exc))
                continue
            self._breakers[model].failures = 0
            await self._record(model, request, "ok", completion)
            return completion, value
        return f"{model}: {_INVALID}"

    async def _call_with_retries[T](
        self, model: ModelRef, request: _Request[T], messages: list[Message]
    ) -> Completion | str:
        breaker = self._breakers[model]
        attempts = self._settings.llm_attempts_per_model
        for attempt in range(1, attempts + 1):
            try:
                return await self._call(model, messages, request.json_mode)
            except RateLimitedError as exc:
                cooldown = exc.retry_after or self._settings.llm_breaker_cooldown_seconds
                breaker.open_until = time.monotonic() + cooldown
                await self._record(model, request, "rate_limited", None, str(exc))
                return f"{model}: rate limited"
            except ProviderError as exc:
                await self._record(model, request, "error", None, str(exc))
                if not exc.retryable or attempt == attempts:
                    self._trip(breaker, force=not exc.retryable)
                    return str(exc)
                await asyncio.sleep(self._backoff(attempt))
        return f"{model}: retries exhausted"

    async def _call(self, model: ModelRef, messages: list[Message], json_mode: bool) -> Completion:
        slot = self._slots.setdefault(
            model.provider, asyncio.Semaphore(self._settings.llm_provider_concurrency)
        )
        async with slot:
            return await self._client.complete(
                model,
                messages,
                temperature=self._settings.llm_temperature,
                max_tokens=self._settings.llm_max_output_tokens,
                json_mode=json_mode,
                timeout_seconds=self._settings.llm_timeout_seconds,
            )

    def _trip(self, breaker: _Breaker, *, force: bool) -> None:
        breaker.failures += 1
        if force or breaker.failures >= self._settings.llm_breaker_failures:
            breaker.open_until = time.monotonic() + self._settings.llm_breaker_cooldown_seconds
            breaker.failures = 0

    def _backoff(self, attempt: int) -> float:
        ceiling: float = min(
            self._settings.llm_max_backoff_seconds,
            self._settings.llm_backoff_seconds * float(2 ** (attempt - 1)),
        )
        return ceiling * random.uniform(0.5, 1.0)  # noqa: S311 - jitter, not security

    async def _record[T](
        self,
        model: ModelRef,
        request: _Request[T],
        outcome: Outcome,
        completion: Completion | None,
        error: str | None = None,
    ) -> None:
        if outcome != "ok":
            log.warning("model call %s", outcome, extra={"model": str(model), "error": error})
        record = CallRecord(
            task=request.task,
            model=model,
            outcome=outcome,
            latency_ms=completion.latency_ms if completion else 0,
            prompt_tokens=completion.prompt_tokens if completion else None,
            completion_tokens=completion.completion_tokens if completion else None,
            error=error,
            tags=request.tags,
        )
        try:
            await self._recorder(record)
        except Exception:
            log.exception("call recorder failed")


def build_router(
    http: httpx.AsyncClient,
    providers: ProviderSettings,
    routes: RouteSettings,
    settings: ModelCallSettings,
    recorder: CallRecorder = _discard,
) -> ModelRouter:
    options = providers.provider_request_options
    endpoints = {
        name: Endpoint(base_url=url, api_key=key, options=options.get(name, {}))
        for name, (url, key) in providers.endpoints().items()
    }
    raw = {
        Task.REASONING: routes.route_reasoning,
        Task.FAST: routes.route_fast,
        Task.VISION: routes.route_vision,
    }
    chains = {
        task: [ModelRef.parse(entry) for entry in value.split(",") if entry.strip()]
        for task, value in raw.items()
    }
    return ModelRouter(ChatClient(http, endpoints), chains, settings, recorder)
