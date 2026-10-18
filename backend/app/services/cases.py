"""Case lifecycle: creation, token checks and retention.

A case is reached with an unguessable ID plus a bearer token issued once at
creation. Only the token's SHA-256 is stored, so a database leak does not grant
access. Expired cases are purged with their vectors.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import CaseSettings
from app.db.models import Case, utcnow
from app.db.session import transaction
from app.errors import AccessDeniedError, NotFoundError
from app.policy.index import PolicyIndex

log = logging.getLogger(__name__)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class CaseService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        index: PolicyIndex,
        settings: CaseSettings,
    ) -> None:
        self._sessions = sessions
        self._index = index
        self._settings = settings

    async def create(self) -> tuple[Case, str]:
        token = secrets.token_urlsafe(32)
        case = Case(
            token_hash=_digest(token),
            expires_at=utcnow() + timedelta(hours=self._settings.case_ttl_hours),
            facts={},
        )
        async with transaction(self._sessions) as session:
            session.add(case)
        return case, token

    async def authorise(self, session: AsyncSession, case_id: str, token: str) -> Case:
        case = await session.scalar(
            select(Case).where(Case.id == case_id).options(selectinload(Case.documents))
        )
        if case is None or case.expires_at <= utcnow():
            raise NotFoundError("case not found or expired")
        if not hmac.compare_digest(case.token_hash, _digest(token)):
            raise AccessDeniedError("invalid case token")
        return case

    async def delete(self, case_id: str) -> None:
        async with transaction(self._sessions) as session:
            await session.execute(delete(Case).where(Case.id == case_id))
        await self._index.delete_case(case_id)

    async def purge_expired(self) -> int:
        async with transaction(self._sessions) as session:
            due = select(Case.id).where(Case.expires_at <= utcnow())
            expired = list(await session.scalars(due))
            if expired:
                await session.execute(delete(Case).where(Case.id.in_(expired)))
        for case_id in expired:
            await self._index.delete_case(case_id)
        if expired:
            log.info("purged expired cases", extra={"count": len(expired)})
        return len(expired)
