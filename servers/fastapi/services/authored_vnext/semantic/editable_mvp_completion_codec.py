"""Canonical parsing and digest helpers for editable-MVP completion seals."""

from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from typing import Any
from utils.hashing import canonical_json_bytes, sha256_prefixed


DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

class EditableMvpCompletionError(ValueError):
    def __init__(self, code: str, *, status_code: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def _canonical_json(value: Any) -> str:
    try:
        return canonical_json_bytes(value).decode("utf-8")
    except (TypeError, ValueError) as error:
        raise EditableMvpCompletionError(
            "editable_mvp_non_canonical_json", status_code=422
        ) from error


def _digest(value: Any) -> str:
    return sha256_prefixed(_canonical_json(value).encode("utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return sha256_prefixed(value)


def _without(value: dict[str, Any], *fields: str) -> dict[str, Any]:
    result = deepcopy(value)
    for field in fields:
        result.pop(field, None)
    return result


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EditableMvpCompletionError(
                "editable_mvp_duplicate_json_key", status_code=422
            )
        value[key] = item
    return value


def _json_bytes(value: bytes, label: str, *, maximum: int) -> dict[str, Any]:
    if not value or len(value) > maximum:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_size_invalid", status_code=422
        )
    try:
        parsed = json.loads(
            value.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates
        )
    except EditableMvpCompletionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_json_invalid", status_code=422
        ) from error
    if not isinstance(parsed, dict):
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_object_required", status_code=422
        )
    return parsed


def _decode(encoded: str, label: str, *, maximum: int) -> bytes:
    try:
        value = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_base64_invalid", status_code=422
        ) from error
    if not value or len(value) > maximum:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_size_invalid", status_code=422
        )
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_invalid", status_code=422
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_invalid", status_code=422
        ) from error
    if parsed.tzinfo is None:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_timezone_required", status_code=422
        )
    return parsed.astimezone(timezone.utc)


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise EditableMvpCompletionError(
            f"editable_mvp_{label}_digest_invalid", status_code=422
        )
    return value
