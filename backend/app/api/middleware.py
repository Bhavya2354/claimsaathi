"""ASGI middleware: request IDs, per-client rate limiting and security headers.

The rate limiter is an in-process token bucket, enough for one API instance.
Running several instances would move the buckets to a shared store such as Redis.
"""

import math
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability import request_id_var

_MAX_CLIENTS = 50_000
_MAX_REQUEST_ID = 64
_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id", "")
        usable = 0 < len(incoming) <= _MAX_REQUEST_ID and incoming.isprintable()
        request_id = incoming if usable else uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["x-request-id"] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            request_id_var.reset(token)


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float


class RateLimitMiddleware:
    def __init__(
        self, app: ASGIApp, *, per_minute: int, burst: int, trusted_proxy_hops: int
    ) -> None:
        self.app = app
        self._rate = per_minute / 60.0
        self._burst = float(burst)
        self._hops = trusted_proxy_hops
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def _client(self, scope: Scope) -> str:
        if self._hops:
            forwarded = Headers(scope=scope).get("x-forwarded-for", "")
            chain = [part.strip() for part in forwarded.split(",") if part.strip()]
            if len(chain) >= self._hops:
                return chain[-self._hops]
        client = scope.get("client")
        return client[0] if client else "unknown"

    def _take(self, key: str) -> float:
        """0 when allowed, else seconds until a token is free."""
        now = time.monotonic()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self._burst, updated=now)
            self._buckets[key] = bucket
        self._buckets.move_to_end(key)
        while len(self._buckets) > _MAX_CLIENTS:
            self._buckets.popitem(last=False)
        bucket.tokens = min(self._burst, bucket.tokens + (now - bucket.updated) * self._rate)
        bucket.updated = now
        if bucket.tokens >= 1:
            bucket.tokens -= 1
            return 0.0
        return (1 - bucket.tokens) / self._rate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method") == "OPTIONS"
            or path.startswith("/health")
        ):
            await self.app(scope, receive, send)
            return
        wait = self._take(self._client(scope))
        if wait > 0:
            response = JSONResponse(
                {"detail": "Too many requests. Please wait a moment and try again."},
                status_code=429,
                headers={"retry-after": str(math.ceil(wait))},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
