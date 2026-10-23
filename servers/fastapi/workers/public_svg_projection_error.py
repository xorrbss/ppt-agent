"""Bounded projection failure context shared by converter and worker boundary."""

from __future__ import annotations

import re
import json
from collections.abc import Mapping
from contextlib import contextmanager

from workers.public_worker_store import PublicWorkerExecutionError


class SvgExportProjectionError(PublicWorkerExecutionError):
    def __init__(self, page_index: int | None, error: BaseException) -> None:
        super().__init__("svg_source_export_projection_invalid")
        self.page_index = page_index
        candidate = getattr(error, "code", None)
        if candidate is None and isinstance(error, ValueError):
            candidate = str(error)
        self.reason_code = (
            candidate if isinstance(candidate, str) and re.fullmatch(r"svg_[a-z0-9_]{1,100}", candidate)
            else type(error).__name__
        )
        self.detail = {"page_index": self.page_index, "reason_code": self.reason_code}


def projection_failure_detail(value: object) -> dict[str, object]:
    """Validate the only projection fields allowed over the worker wire."""
    if not isinstance(value, Mapping) or set(value) != {"page_index", "reason_code"}:
        raise ValueError("svg_export_projection_failure_detail_invalid")
    page_index, reason_code = value["page_index"], value["reason_code"]
    if (page_index is not None and (type(page_index) is not int or not 0 <= page_index < 2**31)):
        raise ValueError("svg_export_projection_failure_detail_invalid")
    if not isinstance(reason_code, str) or not re.fullmatch(
            r"(?:svg_[a-z0-9_]{1,100}|[A-Z][a-zA-Z0-9]{0,63})", reason_code):
        raise ValueError("svg_export_projection_failure_detail_invalid")
    result = {"page_index": page_index, "reason_code": reason_code}
    if len(json.dumps(result).encode("utf-8")) > 512:
        raise ValueError("svg_export_projection_failure_detail_invalid")
    return result


@contextmanager
def projection_page(page_index: int):
    """Attach the known zero-based page exactly where projection executes."""
    try:
        yield
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise SvgExportProjectionError(page_index, error) from error
