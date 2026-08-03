import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from models.api_error_model import APIErrorEnvelope, APIErrorModel
from services.workspace_error_registry import (
    WorkspaceAPIException,
    resolve_workspace_error,
)


REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"
_MAX_REQUEST_ID_LENGTH = 128


def _safe_request_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > _MAX_REQUEST_ID_LENGTH:
        return None
    if not candidate.isascii() or any(ord(char) < 32 for char in candidate):
        return None
    return candidate


def get_request_id(request: Request) -> str:
    existing = _safe_request_id(getattr(request.state, "request_id", None))
    if existing:
        return existing

    request_id = _safe_request_id(request.headers.get(REQUEST_ID_HEADER))
    if not request_id:
        request_id = _safe_request_id(request.headers.get(CORRELATION_ID_HEADER))
    request_id = request_id or str(uuid.uuid4())
    request.state.request_id = request_id
    return request_id


def _error_response(
    request: Request,
    exception: Exception,
    *,
    status_code: int,
    detail: object,
    code: Optional[str] = None,
    message: Optional[str] = None,
    safe_message: Optional[str] = None,
    retryable: Optional[bool] = None,
    headers: Optional[dict[str, str]] = None,
) -> JSONResponse:
    request_id = get_request_id(request)
    error = APIErrorModel.from_exception(
        HTTPException(status_code=status_code, detail=detail),
        request_id=request_id,
        code=code,
        message=message,
        safe_message=safe_message,
        retryable=retryable,
    )
    body = APIErrorEnvelope(
        detail=detail,
        error=error,
        request_id=request_id,
    )
    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True),
        headers=response_headers,
    )


async def _http_exception_handler(
    request: Request, exception: HTTPException
) -> JSONResponse:
    if isinstance(exception, WorkspaceAPIException) or _is_workspace_request(request):
        definition = resolve_workspace_error(
            getattr(exception, "code", exception.detail),
            status_code=exception.status_code,
            retryable=getattr(exception, "retryable", None),
        )
        return _error_response(
            request,
            exception,
            status_code=definition.status_code,
            detail=definition.code,
            code=definition.code,
            message=definition.safe_message,
            safe_message=definition.safe_message,
            retryable=definition.retryable,
            headers=exception.headers,
        )
    return _error_response(
        request,
        exception,
        status_code=exception.status_code,
        detail=exception.detail,
        headers=exception.headers,
    )


async def _validation_exception_handler(
    request: Request, exception: RequestValidationError
) -> JSONResponse:
    if _is_workspace_request(request):
        definition = resolve_workspace_error("request_validation_error")
        # Pydantic errors can include submitted values.  Workspace prompts and
        # metadata are private, so expose only the typed public contract.
        return _error_response(
            request,
            exception,
            status_code=definition.status_code,
            detail=definition.code,
            code=definition.code,
            message=definition.safe_message,
            safe_message=definition.safe_message,
            retryable=definition.retryable,
        )
    return _error_response(
        request,
        exception,
        status_code=422,
        detail=exception.errors(),
        code="request_validation_error",
        message="Request validation failed",
    )


async def _unhandled_exception_handler(
    request: Request, exception: Exception
) -> JSONResponse:
    # Do not expose provider responses, prompts, filesystem paths, or credentials.
    if _is_workspace_request(request):
        definition = resolve_workspace_error("internal_server_error")
        return _error_response(
            request,
            exception,
            status_code=definition.status_code,
            detail=definition.code,
            code=definition.code,
            message=definition.safe_message,
            safe_message=definition.safe_message,
            retryable=definition.retryable,
        )
    return _error_response(
        request,
        exception,
        status_code=500,
        detail="Internal server error",
        code="internal_server_error",
    )


def _is_workspace_request(request: Request) -> bool:
    path = request.url.path.rstrip("/")
    return path == "/workspace" or "/workspace/" in f"{path}/"


def install_api_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next) -> Response:
        request_id = get_request_id(request)
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response

    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
