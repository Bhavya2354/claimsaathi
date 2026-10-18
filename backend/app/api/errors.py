"""Mapping domain errors to HTTP responses. Internal details never reach the client."""

import logging
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import (
    AccessDeniedError,
    AppError,
    DocumentError,
    ModelUnavailableError,
    NotFoundError,
    StructuredOutputError,
    VerificationError,
)
from app.services.claims import MissingDocumentsError

log = logging.getLogger(__name__)

_STATUS: dict[type[AppError], HTTPStatus] = {
    NotFoundError: HTTPStatus.NOT_FOUND,
    AccessDeniedError: HTTPStatus.UNAUTHORIZED,
    DocumentError: HTTPStatus.UNPROCESSABLE_ENTITY,
    MissingDocumentsError: HTTPStatus.CONFLICT,
    ModelUnavailableError: HTTPStatus.SERVICE_UNAVAILABLE,
    StructuredOutputError: HTTPStatus.BAD_GATEWAY,
    VerificationError: HTTPStatus.BAD_GATEWAY,
}

_PUBLIC_MESSAGE = {
    ModelUnavailableError: "All AI models are busy right now. Please try again in a minute.",
    StructuredOutputError: "The AI models could not produce a reliable answer. Please retry.",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        kind = type(exc)
        status = next(
            (code for cls, code in _STATUS.items() if issubclass(kind, cls)),
            HTTPStatus.INTERNAL_SERVER_ERROR,
        )
        if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
            log.warning("request failed", extra={"error": str(exc), "path": request.url.path})
        detail = next((m for cls, m in _PUBLIC_MESSAGE.items() if issubclass(kind, cls)), None)
        return JSONResponse({"detail": detail or str(exc)}, status_code=status)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse(
            {"detail": "Something went wrong on our side."},
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
