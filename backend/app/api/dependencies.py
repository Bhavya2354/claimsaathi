"""Request-scoped dependencies: the container, a database session and case access.

Case access uses its own short session and returns a detached case, so no
transaction stays open while agents spend a minute on model calls.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.container import Container
from app.db.models import Case
from app.errors import AccessDeniedError


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_session(container: ContainerDep) -> AsyncIterator[AsyncSession]:
    async with container.sessions() as session, session.begin():
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_case(
    container: ContainerDep,
    case_id: Annotated[str, Path(min_length=36, max_length=36)],
    authorization: Annotated[str | None, Header()] = None,
) -> Case:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AccessDeniedError("send the case token as `Authorization: Bearer <token>`")
    async with container.sessions() as session:
        return await container.cases.authorise(session, case_id, token.strip())


CaseDep = Annotated[Case, Depends(get_case)]
