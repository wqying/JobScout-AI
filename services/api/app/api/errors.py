from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


async def app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise exc
    envelope = ErrorEnvelope(
        error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
    )
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump(mode="json"))


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code="VALIDATION_ERROR",
            message="The request contains invalid or missing values.",
        )
    )
    return JSONResponse(status_code=422, content=envelope.model_dump())


async def unhandled_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code="INTERNAL_ERROR",
            message="An unexpected local error occurred.",
        )
    )
    return JSONResponse(status_code=500, content=envelope.model_dump())
