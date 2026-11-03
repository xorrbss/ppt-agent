import re
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, model_validator


_STABLE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]*$")
_STRUCTURED_ERROR_CODE = re.compile(
    r"(?=.{1,128}$)[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*(?::[A-Za-z0-9]+(?:_[A-Za-z0-9]+)*){0,3}"
)
_APPLICATION_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_VENDORED_SOURCE_ROOT = _APPLICATION_SOURCE_ROOT / "vendor"


def _is_application_exception(e: Exception) -> bool:
    for exception_class in type(e).__mro__:
        module = sys.modules.get(exception_class.__module__)
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        try:
            source_file = Path(module_file).resolve()
            source_file.relative_to(_APPLICATION_SOURCE_ROOT)
        except (OSError, ValueError):
            continue
        if not source_file.is_file():
            continue
        # ``uv sync`` puts the virtualenv inside servers/fastapi, so installed
        # packages (SQLAlchemy, SDKs) sit under the source root too; their
        # ``code`` attributes are driver-private, not application codes.
        if "site-packages" in source_file.parts or ".venv" in source_file.parts:
            continue
        try:
            source_file.relative_to(_VENDORED_SOURCE_ROOT)
        except ValueError:
            return True
    return False


class APIErrorModel(BaseModel):
    status_code: int
    detail: Any
    code: Optional[str] = None
    message: Optional[str] = None
    filename: Optional[str] = None
    request_id: Optional[str] = None
    safe_message: Optional[str] = None
    retryable: Optional[bool] = None

    def model_dump(self, *args, **kwargs):
        # Preserve the historical Python/wire shape when the workspace-only
        # fields are not populated.
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(*args, **kwargs)

    @model_validator(mode="after")
    def fill_compatible_defaults(self) -> "APIErrorModel":
        structured_detail = self.detail if isinstance(self.detail, dict) else {}
        detail_code = structured_detail.get("code")
        if not self.code:
            self.code = (
                detail_code
                if isinstance(detail_code, str)
                and _STABLE_ERROR_CODE.fullmatch(detail_code)
                else (
                    self.detail
                    if isinstance(self.detail, str)
                    and _STABLE_ERROR_CODE.fullmatch(self.detail)
                    else f"http_error_{self.status_code}"
                )
            )
        if not self.message:
            detail_message = structured_detail.get("message")
            self.message = detail_message if isinstance(detail_message, str) else (
                self.detail if isinstance(self.detail, str) else "Request failed"
            )
        if self.filename is None:
            detail_filename = structured_detail.get("filename")
            if isinstance(detail_filename, str):
                self.filename = detail_filename
        if self.retryable is None:
            detail_retryable = structured_detail.get("retryable")
            if isinstance(detail_retryable, bool):
                self.retryable = detail_retryable
        return self

    @classmethod
    def from_exception(
        cls,
        e: Exception,
        *,
        request_id: Optional[str] = None,
        code: Optional[str] = None,
        message: Optional[str] = None,
        safe_message: Optional[str] = None,
        retryable: Optional[bool] = None,
    ) -> "APIErrorModel":
        if isinstance(e, HTTPException):
            status_code = e.status_code
            detail = e.detail
        else:
            status_code = 500
            detail = str(e)

        # HTTP boundaries attach classified codes explicitly. Application-owned
        # exception types also declare codes; driver/SDK attributes are private.
        application_error = _is_application_exception(e)
        exception_code = getattr(e, "code", None) if isinstance(e, HTTPException) or application_error else None
        if code is None and isinstance(exception_code, str) and _STRUCTURED_ERROR_CODE.fullmatch(exception_code):
            code = exception_code
        if code is None and not isinstance(e, HTTPException):
            # Do not let the model validator infer a code from raw exception text.
            code = f"http_error_{status_code}"

        return APIErrorModel(
            status_code=status_code,
            detail=detail,
            code=code,
            message=message,
            request_id=request_id,
            safe_message=safe_message,
            retryable=retryable,
        )


class APIErrorEnvelope(BaseModel):
    """Common HTTP error body.

    ``detail`` remains at the top level for existing clients while new clients
    consume the structured error and correlation id.
    """

    detail: Any
    error: APIErrorModel
    request_id: str
