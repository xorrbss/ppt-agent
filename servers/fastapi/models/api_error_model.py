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
    request_id: Optional[str] = None

    @model_validator(mode="after")
    def fill_compatible_defaults(self) -> "APIErrorModel":
        if not self.code:
            self.code = (
                self.detail
                if isinstance(self.detail, str)
                and _STABLE_ERROR_CODE.fullmatch(self.detail)
                else f"http_error_{self.status_code}"
            )
        if not self.message:
            self.message = (
                self.detail if isinstance(self.detail, str) else "Request failed"
            )
        return self

    @classmethod
    def from_exception(
        cls,
        e: Exception,
        *,
        request_id: Optional[str] = None,
        code: Optional[str] = None,
        message: Optional[str] = None,
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
        )


class APIErrorEnvelope(BaseModel):
    """Common HTTP error body.

    ``detail`` remains at the top level for existing clients while new clients
    consume the structured error and correlation id.
    """

    detail: Any
    error: APIErrorModel
    request_id: str
