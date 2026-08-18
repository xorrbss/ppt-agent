"""Narrate an authored-hybrid export while Next.js runs it.

The export's long phases execute inside the Next.js process behind a single
blocking POST, so this side of the call has nothing to report between "started"
and "finished" — for a 100-slide deck that silence lasts minutes and reads as a
hung job. The poller reads the exporter's in-process progress and turns it into
the same ``operation.progress`` events every other workspace operation emits.

Progress is strictly advisory. Every failure here is swallowed: a poll that
times out, 404s, or returns nonsense costs the user narration, never the
export.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable, Protocol

import httpx

from utils.export_utils import _get_next_public_url

LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 3.0
_POLL_TIMEOUT_SECONDS = 5.0

# The percentage band this poller owns. Below it the handler has already
# reported acceptance; above it publication takes over.
_PROGRESS_FLOOR = 10
_PROGRESS_CEILING = 90
# Share of the band spent on the fidelity render. Measured runs put it at
# roughly a third of wall time, with the per-slide pass taking the rest.
_FIDELITY_BAND_SHARE = 0.25


class ProgressReporter(Protocol):
    """Matches ``WorkspaceOperationContext.progress``."""

    async def __call__(
        self,
        *,
        stage: str,
        percent: int | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> bool | None: ...


def _band(fraction: float) -> int:
    span = _PROGRESS_CEILING - _PROGRESS_FLOOR
    return _PROGRESS_FLOOR + int(round(span * max(0.0, min(1.0, fraction))))


def describe_export_progress(
    snapshot: dict,
) -> tuple[str, int, str, int | None, int | None]:
    """Map a progress snapshot to (stage, percent, message, current, total)."""

    phase = snapshot.get("phase")
    total = snapshot.get("totalSlides")
    completed = snapshot.get("completedSlides")
    if not isinstance(total, int) or total <= 0:
        total = None
    if not isinstance(completed, int) or completed < 0:
        completed = None

    if phase == "fidelity":
        return (
            "export_fidelity_render",
            _band(_FIDELITY_BAND_SHARE / 2),
            "슬라이드를 렌더링하고 있습니다.",
            None,
            None,
        )
    if phase == "slides":
        if total is None or completed is None:
            fraction = _FIDELITY_BAND_SHARE
            message = "편집 가능 요소를 추출하고 있습니다."
        else:
            fraction = _FIDELITY_BAND_SHARE + (
                (1 - _FIDELITY_BAND_SHARE) * (completed / total)
            )
            message = f"편집 가능 요소 추출 중 · {completed}/{total}장"
        return ("export_native_extraction", _band(fraction), message, completed, total)
    if phase == "assembly":
        return (
            "export_assembly",
            _PROGRESS_CEILING,
            "PowerPoint 파일을 조립하고 있습니다.",
            total,
            total,
        )
    if phase in {"completed", "failed"}:
        return (
            "export_render_finished",
            _PROGRESS_CEILING,
            "내보내기 파일을 저장하고 있습니다.",
            total,
            total,
        )
    return (
        "export_rendering",
        _PROGRESS_FLOOR,
        "내보내기를 준비하고 있습니다.",
        None,
        None,
    )


async def _poll_once(
    client: httpx.AsyncClient,
    url: str,
    operation_id: uuid.UUID,
) -> dict | None:
    response = await client.get(url, params={"operationId": str(operation_id)})
    if response.status_code != 200:
        return None
    payload = response.json()
    return payload if isinstance(payload, dict) else None


async def stream_export_progress(
    *,
    operation_id: uuid.UUID,
    cookie_header: str | None,
    report: ProgressReporter,
    interval_seconds: float = _POLL_INTERVAL_SECONDS,
) -> None:
    """Poll the exporter until cancelled, reporting each distinct change.

    Cancellation is the normal exit: the caller cancels this task once the
    export request returns.
    """

    url = f"{_get_next_public_url().rstrip('/')}/api/internal/export-progress"
    headers = {"cookie": cookie_header} if cookie_header else {}
    last_reported: tuple[str, int, str] | None = None
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_POLL_TIMEOUT_SECONDS),
            headers=headers,
        ) as client:
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    snapshot = await _poll_once(client, url, operation_id)
                except (httpx.HTTPError, ValueError):
                    # The export is still running; only its narration is blind.
                    continue
                if snapshot is None:
                    continue
                stage, percent, message, current, total = describe_export_progress(
                    snapshot
                )
                if (stage, percent, message) == last_reported:
                    continue
                last_reported = (stage, percent, message)
                try:
                    await report(
                        stage=stage,
                        percent=percent,
                        current=current,
                        total=total,
                        message=message,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # A lost lease or a closed session is the runner's problem
                    # to surface; progress must never be the thing that fails
                    # an export.
                    LOGGER.debug(
                        "Export progress report failed for operation %s",
                        operation_id,
                        exc_info=True,
                    )
                    return
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.debug(
            "Export progress polling stopped for operation %s",
            operation_id,
            exc_info=True,
        )


def start_export_progress_polling(
    *,
    operation_id: uuid.UUID,
    cookie_header: str | None,
    report: ProgressReporter,
) -> Callable[[], Awaitable[None]]:
    """Start polling and return an idempotent stopper for the caller's finally."""

    task = asyncio.create_task(
        stream_export_progress(
            operation_id=operation_id,
            cookie_header=cookie_header,
            report=report,
        )
    )

    async def stop() -> None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    return stop
