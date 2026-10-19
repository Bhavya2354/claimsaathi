"""HTTP client for OpenAI-compatible chat endpoints.

Groq, Gemini, Ollama, OpenRouter, Cerebras, Mistral and GitHub Models all expose
`/chat/completions` in this shape, so one client covers every provider.
"""

import re
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx
from pydantic import SecretStr

from app.llm.types import Completion, Message, ModelRef

_THINK_BLOCK = re.compile(r"<(think|thought)>.*?</>", re.DOTALL | re.IGNORECASE)


class ProviderError(Exception):
    """A provider call failed. `retryable` tells the router whether to try the same model again."""

    def __init__(self, message: str, *, status: int | None, retryable: bool) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class RateLimitedError(ProviderError):
    def __init__(self, message: str, retry_after: float | None) -> None:
        super().__init__(message, status=429, retryable=False)
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Endpoint:
    base_url: str
    api_key: SecretStr | None


def _encode(message: Message) -> dict[str, Any]:
    if not message.images:
        return {"role": message.role, "content": message.content}
    parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
    parts.extend(
        {"type": "image_url", "image_url": {"url": image.data_url()}} for image in message.images
    )
    return {"role": message.role, "content": parts}


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class ChatClient:
    def __init__(self, http: httpx.AsyncClient, endpoints: dict[str, Endpoint]) -> None:
        self._http = http
        self._endpoints = endpoints
        # Models that rejected `response_format`; they still return JSON when the prompt asks.
        self._no_json_mode: set[ModelRef] = set()

    def has_provider(self, provider: str) -> bool:
        return provider in self._endpoints

    async def complete(
        self,
        model: ModelRef,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        timeout_seconds: float,
    ) -> Completion:
        endpoint = self._endpoints[model.provider]
        body: dict[str, Any] = {
            "model": model.model,
            "messages": [_encode(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and model not in self._no_json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if endpoint.api_key is not None:
            headers["Authorization"] = f"Bearer {endpoint.api_key.get_secret_value()}"

        started = time.perf_counter()
        try:
            response = await self._http.post(
                f"{endpoint.base_url}/chat/completions",
                json=body,
                headers=headers,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise ProviderError(f"{model}: timed out", status=None, retryable=True) from exc
        except httpx.TransportError as exc:
            reason = f"{model}: {type(exc).__name__}"
            raise ProviderError(reason, status=None, retryable=True) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise RateLimitedError(f"{model}: rate limited", _retry_after(response))
        if response.status_code == HTTPStatus.BAD_REQUEST and "response_format" in body:
            self._no_json_mode.add(model)
            return await self.complete(
                model,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
                timeout_seconds=timeout_seconds,
            )
        if response.status_code >= HTTPStatus.BAD_REQUEST:
            detail = response.text[:300].replace("\n", " ")
            raise ProviderError(
                f"{model}: HTTP {response.status_code} {detail}",
                status=response.status_code,
                retryable=response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        payload = response.json()
        try:
            text = payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{model}: malformed response", status=200, retryable=True) from exc
        usage = payload.get("usage") or {}
        return Completion(
            text=_THINK_BLOCK.sub("", text).strip(),
            model=model,
            latency_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
