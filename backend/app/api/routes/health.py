from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.dependencies import ContainerDep

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(container: ContainerDep) -> JSONResponse:
    """Database reachable and at least one model provider enabled on every route."""
    checks: dict[str, object] = {}
    try:
        async with container.sessions() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    routes = container.router.summary()
    checks["models"] = {task: len(chain) for task, chain in routes.items()}
    healthy = checks["database"] == "ok" and all(routes.values())
    return JSONResponse(
        {"status": "ready" if healthy else "degraded", **checks},
        status_code=200 if healthy else 503,
    )
