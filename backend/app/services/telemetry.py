"""Recording every model attempt: always to the database, optionally to Langfuse.

Only metadata leaves the process (agent, model, latency, tokens, outcome); prompt
and document text are never sent to the tracing service. Langfuse events are
buffered and flushed in the background so tracing never adds request latency.
One trace per API request, keyed by the request ID.
"""

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import TracingSettings
from app.db.models import ModelCall
from app.db.session import transaction
from app.llm.router import CallRecorder
from app.llm.types import CallRecord
from app.observability import request_id_var

log = logging.getLogger(__name__)

_FLUSH_SECONDS = 5.0
_MAX_BATCH = 50
_MAX_TRACKED_TRACES = 10_000


class DatabaseCallRecorder:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def __call__(self, record: CallRecord) -> None:
        async with transaction(self._sessions) as session:
            session.add(
                ModelCall(
                    case_id=record.tags.get("case"),
                    agent=record.tags.get("agent"),
                    task=record.task.value,
                    provider=record.model.provider,
                    model=record.model.model,
                    outcome=record.outcome,
                    latency_ms=record.latency_ms,
                    prompt_tokens=record.prompt_tokens,
                    completion_tokens=record.completion_tokens,
                    error=(record.error or "")[:500] or None,
                )
            )


class LangfuseRecorder:
    """Sends generation events to Langfuse's public ingestion API in batches."""

    def __init__(self, http: httpx.AsyncClient, settings: TracingSettings) -> None:
        host, public, secret = (
            settings.langfuse_host,
            settings.langfuse_public_key,
            settings.langfuse_secret_key,
        )
        if host is None or public is None or secret is None:
            raise ValueError("Langfuse settings are incomplete")
        self._http = http
        self._url = f"{str(host).rstrip('/')}/api/public/ingestion"
        self._auth = (public.get_secret_value(), secret.get_secret_value())
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5_000)
        self._traces: set[str] = set()
        self._task: asyncio.Task[None] | None = None

    async def __call__(self, record: CallRecord) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._flush_forever())
        trace_id = request_id_var.get() or record.tags.get("case") or uuid.uuid4().hex
        now = datetime.now(UTC)
        if trace_id not in self._traces:
            if len(self._traces) > _MAX_TRACKED_TRACES:
                self._traces.clear()
            self._traces.add(trace_id)
            self._put(
                "trace-create",
                {"id": trace_id, "name": "claim-request", "sessionId": record.tags.get("case")},
                now,
            )
        self._put(
            "generation-create",
            {
                "id": uuid.uuid4().hex,
                "traceId": trace_id,
                "name": record.tags.get("agent", record.task.value),
                "model": str(record.model),
                "startTime": (now - timedelta(milliseconds=record.latency_ms)).isoformat(),
                "endTime": now.isoformat(),
                "usage": {
                    "input": record.prompt_tokens,
                    "output": record.completion_tokens,
                    "unit": "TOKENS",
                },
                "metadata": {k: v for k, v in record.tags.items() if k != "case"},
                "level": "DEFAULT" if record.outcome == "ok" else "WARNING",
                "statusMessage": record.outcome if record.outcome != "ok" else None,
            },
            now,
        )

    def _put(self, kind: str, body: dict[str, Any], at: datetime) -> None:
        event = {"id": uuid.uuid4().hex, "type": kind, "timestamp": at.isoformat(), "body": body}
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(event)

    async def _flush_forever(self) -> None:
        while True:
            await asyncio.sleep(_FLUSH_SECONDS)
            await self.flush()

    async def flush(self) -> None:
        batch: list[dict[str, Any]] = []
        while not self._queue.empty() and len(batch) < _MAX_BATCH:
            batch.append(self._queue.get_nowait())
        if not batch:
            return
        try:
            response = await self._http.post(
                self._url, json={"batch": batch}, auth=self._auth, timeout=10.0
            )
            if response.status_code >= httpx.codes.BAD_REQUEST:
                log.warning("langfuse rejected a batch", extra={"status": response.status_code})
        except httpx.HTTPError:
            log.warning("langfuse unreachable; dropped a trace batch")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()


class FanOutRecorder:
    def __init__(self, *recorders: CallRecorder) -> None:
        self._recorders = recorders

    async def __call__(self, record: CallRecord) -> None:
        for recorder in self._recorders:
            try:
                await recorder(record)
            except Exception:
                log.exception("call recorder failed")
