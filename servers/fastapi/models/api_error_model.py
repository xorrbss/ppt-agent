import re
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, model_validator


_STABLE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]*$")


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
