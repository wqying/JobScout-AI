from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.api.routes.assisted_sources import router as assisted_sources_router
from app.api.routes.companies import router as companies_router
from app.api.routes.discoveries import router as discoveries_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as jobs_router
from app.api.routes.notifications import router as notifications_router
from app.api.routes.operations import router as operations_router
from app.api.routes.profile import router as profile_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger("jobscout.lifecycle")
    if settings.allow_remote_access:
        logger.warning(
            "remote_access_flag_enabled",
            warning="V1 has no authentication; rely on a loopback-only host boundary.",
        )
    logger.info("api_started", app_env=settings.app_env)
    yield
    await get_engine().dispose()
    logger.info("api_stopped")


app = FastAPI(
    title="JobScout AI API",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_exception_handler(Exception, unhandled_error_handler)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.include_router(health_router, prefix="/api/v1")
app.include_router(profile_router, prefix="/api/v1")
app.include_router(assisted_sources_router, prefix="/api/v1")
app.include_router(companies_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(discoveries_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(operations_router, prefix="/api/v1")
