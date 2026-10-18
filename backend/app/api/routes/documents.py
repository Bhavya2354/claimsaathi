from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, UploadFile, status

from app.api.dependencies import CaseDep, ContainerDep
from app.config import case_settings
from app.db.models import Case
from app.db.session import transaction
from app.domain import DocumentKind
from app.errors import DocumentError, NotFoundError
from app.services.reads import document_view
from app.services.views import DocumentView

router = APIRouter(prefix="/api/cases/{case_id}/documents", tags=["documents"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def upload(
    case: CaseDep,
    container: ContainerDep,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File()],
    kind: Annotated[DocumentKind | None, Form()] = None,
) -> DocumentView:
    """Checks the file now and processes it in the background; poll the case for status."""
    limit = case_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise DocumentError(f"files above {case_settings().max_upload_mb} MB are not accepted")
    async with transaction(container.sessions) as session:
        document = await container.ingest.accept(session, case, data, file.filename)
        view = document_view(document)
    # Raw bytes stay in memory only for the task; they are never written to disk.
    background.add_task(container.ingest.process, document.id, data, kind)
    return view


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(document_id: str, case: CaseDep, container: ContainerDep) -> None:
    async with transaction(container.sessions) as session:
        stored = await session.get(Case, case.id)
        if stored is None:
            raise NotFoundError("case not found")
        await container.ingest.remove(session, stored, document_id)
