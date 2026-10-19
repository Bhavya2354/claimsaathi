import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import BaseModel

from app.config import ModelCallSettings
from app.errors import ModelUnavailableError, StructuredOutputError
from app.llm.client import ChatClient, Endpoint
from app.llm.router import ModelRouter
from app.llm.types import CallRecord, Message, ModelRef, Task

Handler = Callable[[httpx.Request], httpx.Response]


def _answer(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


def _router(
    handler: Handler, chain: list[str], records: list[CallRecord] | None = None
) -> ModelRouter:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    endpoints = {
        "alpha": Endpoint("http://alpha.test/v1", None),
        "beta": Endpoint("http://beta.test/v1", None),
    }
    settings = ModelCallSettings(
        llm_attempts_per_model=2,
        llm_backoff_seconds=0.001,
        llm_max_backoff_seconds=0.001,
        llm_breaker_cooldown_seconds=60,
    )

    async def record(entry: CallRecord) -> None:
        if records is not None:
            records.append(entry)

    return ModelRouter(
        ChatClient(http, endpoints),
        {Task.FAST: [ModelRef.parse(entry) for entry in chain]},
        settings,
        record,
    )


MESSAGES = [Message(role="system", content="s"), Message(role="user", content="q")]


class Answer(BaseModel):
    value: int


async def test_rate_limit_fails_over_and_cools_down() -> None:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        if request.url.host == "alpha.test":
            return httpx.Response(429, headers={"retry-after": "30"})
        return _answer("from beta")

    router = _router(handler, ["alpha:m1", "beta:m2"])
    first = await router.complete(Task.FAST, MESSAGES, use_cache=False)
    second = await router.complete(Task.FAST, MESSAGES, use_cache=False)
    assert first.text == second.text == "from beta"
    assert str(first.model) == "beta:m2"
    assert hits == ["alpha.test", "beta.test", "beta.test"]  # alpha skipped while cooling


async def test_server_errors_are_retried_on_the_same_model() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else _answer("ok")

    router = _router(handler, ["alpha:m1"])
    assert (await router.complete(Task.FAST, MESSAGES)).text == "ok"
    assert calls["n"] == 2


async def test_every_model_failing_raises() -> None:
    router = _router(lambda _: httpx.Response(500), ["alpha:m1", "beta:m2"])
    with pytest.raises(ModelUnavailableError):
        await router.complete(Task.FAST, MESSAGES)


async def test_invalid_json_gets_one_repair_turn() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        return _answer("not json" if len(bodies) == 1 else '```json\n{"value": 7}\n```')

    records: list[CallRecord] = []
    router = _router(handler, ["alpha:m1"], records)
    result = await router.structured(Task.FAST, MESSAGES, Answer)
    assert result.value == 7
    assert len(bodies) == 2
    assert bodies[1]["messages"][-1]["role"] == "user"  # type: ignore[index]
    assert [r.outcome for r in records] == ["invalid_output", "ok"]


async def test_structured_output_error_after_all_repairs_fail() -> None:
    router = _router(lambda _: _answer('{"value": "seven"}'), ["alpha:m1"])
    with pytest.raises(StructuredOutputError):
        await router.structured(Task.FAST, MESSAGES, Answer)


async def test_json_mode_rejected_is_retried_without_it() -> None:
    seen: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        has_format = "response_format" in json.loads(request.content)
        seen.append(has_format)
        return httpx.Response(400) if has_format else _answer('{"value": 1}')

    router = _router(handler, ["alpha:m1"])
    assert (await router.structured(Task.FAST, MESSAGES, Answer)).value == 1
    assert seen == [True, False]


async def test_answers_are_cached_across_calls() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _answer("cached")

    router = _router(handler, ["alpha:m1"])
    await router.complete(Task.FAST, MESSAGES)
    again = await router.complete(Task.FAST, MESSAGES)
    assert again.cached
    assert calls["n"] == 1


async def test_reasoning_blocks_are_stripped() -> None:
    router = _router(lambda _: _answer("<think>hidden</think> visible"), ["alpha:m1"])
    assert (await router.complete(Task.FAST, MESSAGES)).text == "visible"


def test_route_entries_keep_colons_in_model_ids() -> None:
    ref = ModelRef.parse("ollama:qwen3.5:9b")
    assert (ref.provider, ref.model) == ("ollama", "qwen3.5:9b")


def test_disabled_providers_are_dropped_from_chains() -> None:
    router = _router(lambda _: _answer("x"), ["alpha:m1", "gamma:m3", "beta:m2"])
    assert [str(m) for m in router.chain(Task.FAST)] == ["alpha:m1", "beta:m2"]


async def test_provider_options_are_sent_with_every_request() -> None:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return _answer("ok")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    endpoint = Endpoint("http://local.test/v1", None, {"reasoning_effort": "none"})
    router = ModelRouter(
        ChatClient(http, {"local": endpoint}),
        {Task.FAST: [ModelRef.parse("local:m")]},
        ModelCallSettings(),
    )
    await router.complete(Task.FAST, MESSAGES)
    assert sent[0]["reasoning_effort"] == "none"
