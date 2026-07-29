"""Client for the Next.js Template V2 layout-code validator."""

from __future__ import annotations

import os
from typing import Any

import aiohttp
from pydantic import BaseModel

from api.v1.auth.internal import authenticated_internal_request_headers


class ValidatedLayoutCode(BaseModel):
    layout_code: str
    layout_id: str
    layout_name: str
    layout_description: str
    schema_json_value: Any


class LayoutCodeValidationError(Exception):
    def __init__(
        self,
        error: str,
        *,
        line: int | None = None,
        column: int | None = None,
    ):
        self.error = error
        self.line = line
        self.column = column
        super().__init__(error)

    def to_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"error": self.error}
        if self.line is not None:
            detail["line"] = self.line
        if self.column is not None:
            detail["column"] = self.column
        return detail


class LayoutCodeValidationServiceError(Exception):
    pass


def _endpoint_from_base(base_url: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if normalized.endswith("/api/validate-layout-code"):
        return normalized
    return f"{normalized}/api/validate-layout-code"


def _validation_url_candidates() -> list[str]:
    explicit_url = os.getenv("LAYOUT_CODE_VALIDATION_URL")
    if explicit_url and explicit_url.strip():
        return [_endpoint_from_base(explicit_url)]

    candidates = [
        os.getenv("NEXT_PUBLIC_URL"),
        os.getenv("PRESENTON_NEXT_URL"),
        "http://localhost",
        "http://localhost:3000",
    ]
    endpoints: list[str] = []
    for candidate in candidates:
        if not candidate or not candidate.strip():
            continue
        endpoint = _endpoint_from_base(candidate)
        if endpoint not in endpoints:
            endpoints.append(endpoint)
    return endpoints


def _coerce_validation_error(
    payload: Any,
    fallback: str,
) -> LayoutCodeValidationError:
    if not isinstance(payload, dict):
        return LayoutCodeValidationError(fallback)
    error = payload.get("error")
    line = payload.get("line")
    column = payload.get("column")
    return LayoutCodeValidationError(
        error if isinstance(error, str) and error else fallback,
        line=line if isinstance(line, int) else None,
        column=column if isinstance(column, int) else None,
    )


async def validate_layout_code(layout_code: str) -> ValidatedLayoutCode:
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {
        "Content-Type": "application/json",
        **(await authenticated_internal_request_headers()),
    }
    last_error: Exception | None = None

    for url in _validation_url_candidates():
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    json={"layout_code": layout_code},
                    headers=headers,
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status == 200 and isinstance(payload, dict):
                        return ValidatedLayoutCode(
                            layout_code=payload["layout_code"],
                            layout_id=payload["layoutId"],
                            layout_name=payload["layoutName"],
                            layout_description=payload["layoutDescription"],
                            schema_json_value=payload["schemaJSON"],
                        )
                    if response.status == 400:
                        raise _coerce_validation_error(
                            payload,
                            "Layout code is invalid",
                        )
                    last_error = LayoutCodeValidationServiceError(
                        f"Layout validation service returned {response.status}"
                    )
        except LayoutCodeValidationError:
            raise
        except (aiohttp.ClientError, TimeoutError, KeyError, TypeError) as error:
            last_error = error
        except Exception as error:
            last_error = error

    raise LayoutCodeValidationServiceError(
        "Layout validation service is unavailable"
    ) from last_error
