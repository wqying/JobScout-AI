from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["jobscout-api"] = "jobscout-api"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "error"]]


@router.get("/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(response: Response) -> ReadinessResponse:
    checks: dict[str, Literal["ok", "error"]] = {"postgres": "error", "redis": "error"}

    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        pass

    redis = Redis.from_url(get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1)
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        pass
    finally:
        await redis.aclose()

    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ready" if ready else "not_ready", checks=checks)
