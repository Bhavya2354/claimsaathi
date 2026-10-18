"""Persisting every model attempt, for tracing and for the model comparison report."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ModelCall
from app.db.session import transaction
from app.llm.types import CallRecord

log = logging.getLogger(__name__)


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
