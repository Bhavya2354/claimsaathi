"""ASGI application: `uvicorn app.main:app`."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.container import Container, build_container
from app.api.errors import install_error_handlers
from app.api.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.api.routes import cases, claims, documents, health
from app.config import api_settings, case_settings, runtime_settings
from app.observability import configure_logging

log = logging.getLogger(__name__)


async def _purge_loop(container: Container, every_seconds: float) -> None:
    while True:
        try:
            await container.cases.purge_expired()
        except Exception:
            log.exception("case purge failed")
        await asyncio.sleep(every_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    container = build_container()
    app.state.container = container
    log.info("model routes", extra={"routes": container.router.summary()})
    warm = asyncio.create_task(container.index.warm_up())
    purge = asyncio.create_task(_purge_loop(container, case_settings().purge_interval_minutes * 60))
    try:
        yield
    finally:
        for task in (warm, purge):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await container.close()


def create_app() -> FastAPI:
    configure_logging(runtime_settings())
    settings = api_settings()
    app = FastAPI(
        title="ClaimSaathi API",
        summary="Health insurance bill audits, policy answers and appeal letters with citations.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        RateLimitMiddleware,
        per_minute=settings.api_rate_limit_per_minute,
        burst=settings.api_rate_limit_burst,
        trusted_proxy_hops=settings.api_trusted_proxy_hops,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    for module in (health, cases, documents, claims):
        app.include_router(module.router)
    return app


app = create_app()
