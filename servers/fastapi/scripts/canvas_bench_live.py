"""Live side of the canvas bench: drive one deck through the public API.

Mirrors ``scripts/ppt-agent.mjs`` (async generate, status poll, chained
``export.pptx`` operation followed on the presentation timeline) so a bench
deck is produced exactly the way an API client produces one, then reads the
evidence the server already publishes: the generation operation's
``generation_timing_receipt`` (attempt number, provider retries, flagged
slides, fit summary), the job's QualityReceipt ledger under
``<app-data>/authored-quality-receipts`` and the exported PPTX itself.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
from scripts import canvas_bench_watch as watch
from models.document_canvas import (
    DOCUMENT_CANVAS_CONTRACT_VERSION,
    DOCUMENT_CANVAS_CONTRACTS_HEADER,
)

POLL_SECONDS = 5.0
# uvicorn closes an idle keep-alive connection after 5 s, the same interval
# the bench polls at; with httpx's default 5 s pool expiry the client
# occasionally reuses a socket the server is closing and gets WinError 10054.
# Expiring pooled connections well before the server does removes the race.
KEEPALIVE_EXPIRY_SECONDS = 1.0
# Upper bound on waiting for a completed task's export annotation and
# published generation result (see BenchClient._settle).
SETTLE_SECONDS = 120.0
TERMINAL_OPERATION_STATES = {"succeeded", "failed", "cancelled", "dead_letter"}
REQUEST_TIMEOUT_SECONDS = 120.0
QUALITY_RECEIPT_DIR = "authored-quality-receipts"


class BenchClient:
    def __init__(self, base: str, *, timeout_seconds: float) -> None:
        self.watch = watch.DeckWatch()
        self.finished_seconds: list[float] = []
        self.recovery_deadline = None
        self.base = base.rstrip("/")
        self.deadline_seconds = timeout_seconds
        self.http = httpx.Client(
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
            limits=httpx.Limits(keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS),
            trust_env=False,
        )

    def close(self) -> None:
        self.http.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self.watch.begin()
        try:
            return self.http.request(method, f"{self.base}{path}", **kwargs)
        finally:
            self.watch.end()
            if self.watch.detected and self.recovery_deadline is None:
                self.recovery_deadline = time.monotonic() + watch.LOST_TASK_SETTLE_SECONDS

    def _get(self, path: str) -> Any:
        response = self._request("GET", path)
        response.raise_for_status()
        return response.json()

    def generate(self, body: Mapping[str, Any]) -> dict[str, Any]:
        """Submit, poll to a terminal status, and return the status record."""

        self.watch = watch.DeckWatch()
        self.recovery_deadline = None
        response = self._request("POST", "/api/v1/ppt/presentation/generate/async", json=dict(body))
        if response.status_code >= 400:
            return {"status": "rejected", "http_status": response.status_code, "detail": response.text[:800]}
        admitted = response.json()
        task_id = admitted["id"]
        deadline = time.monotonic() + self.deadline_seconds
        while True:
            status = self._get(f"/api/v1/ppt/presentation/status/{task_id}")
            if status.get("status") in {"completed", "error"}:
                if status.get("status") == "completed":
                    status = self._settle(task_id, status, admitted.get("operation_id"), bool(body.get("export_as")))
                status["operation_id"] = admitted.get("operation_id")
                return status
            if self.recovery_deadline is not None and time.monotonic() > self.recovery_deadline:
                raise ConnectionError("lost deck did not reach terminal status within recovery budget")
            if self.recovery_deadline is None and time.monotonic() > deadline:
                return {"status": "timeout", "id": task_id, "operation_id": admitted.get("operation_id")}
            time.sleep(POLL_SECONDS)

    def _settle(self, task_id: str, status: dict[str, Any], operation_id: str | None,
                export_requested: bool) -> dict[str, Any]:
        """Wait until a completed task's later commits are visible.

        ``completed`` is committed before the chained export annotates the task
        (``export_operation_id``/``export_error``,
        services/presentation_generation_export_chain.py) and before the
        generation operation publishes its result (timing receipt).  Reading
        either the instant ``completed`` appears can see neither, which would
        be recorded as ``export_not_chained`` and a missing timing receipt.
        """

        deadline = time.monotonic() + SETTLE_SECONDS
        while True:
            data = status.get("data") or {}
            # The completion commit already carries both keys as null.
            annotated = not export_requested or bool(data.get("export_operation_id") or data.get("export_error"))
            presentation_id = data.get("presentation_id")
            published = not operation_id or not presentation_id or (
                (operation_card(self.timeline(str(presentation_id)), operation_id=operation_id) or {}).get("status")
                in TERMINAL_OPERATION_STATES
            )
            if self.recovery_deadline is not None and time.monotonic() > self.recovery_deadline:
                if not (annotated and published):
                    raise ConnectionError("lost generation operation did not settle within recovery budget")
            if (annotated and published) or (self.recovery_deadline is None and time.monotonic() > deadline):
                return status
            time.sleep(POLL_SECONDS)
            status = self._get(f"/api/v1/ppt/presentation/status/{task_id}")

    def timeline(self, presentation_id: str) -> dict[str, Any]:
        return self._get(f"/api/v1/ppt/workspace/presentations/{presentation_id}/timeline")

    def presentation(self, presentation_id: str) -> dict[str, Any]:
        # An A4 deck is withheld (409) from readers that do not advertise the
        # document-canvas contract.
        response = self._request(
            "GET", f"/api/v1/ppt/presentation/{presentation_id}",
            headers={DOCUMENT_CANVAS_CONTRACTS_HEADER: DOCUMENT_CANVAS_CONTRACT_VERSION},
        )
        response.raise_for_status()
        return response.json()

    def wait_operation(self, presentation_id: str, operation_id: str) -> dict[str, Any] | None:
        deadline = time.monotonic() + self.deadline_seconds
        while True:
            item = operation_card(self.timeline(presentation_id), operation_id=operation_id)
            if item is not None and item.get("status") in TERMINAL_OPERATION_STATES:
                return item
            if time.monotonic() >= (self.recovery_deadline or deadline):
                if self.recovery_deadline is not None:
                    raise ConnectionError("lost export operation did not settle within recovery budget")
                return None
            time.sleep(POLL_SECONDS)

    def health_probe(self) -> None:
        # Both successful checks are required before another serial deck starts.
        for index in range(2):
            if index:
                time.sleep(watch.HEALTH_PROBE_INTERVAL_SECONDS)
            response = self.http.get(f"{self.base}/health")
            response.raise_for_status()

    def download_artifact(self, artifact_id: str, dest: Path) -> Path:
        response = self._request("GET", f"/api/v1/ppt/workspace/artifacts/{artifact_id}/download")
        response.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return dest


def operation_card(
    timeline: Mapping[str, Any],
    *,
    operation_id: str | None = None,
    operation_type: str | None = None,
) -> dict[str, Any] | None:
    """The newest operation card matching an id or a type."""

    found = None
    for item in timeline.get("items") or []:
        if item.get("anchor_type") != "operation":
            continue
        if operation_id is not None and item.get("operation_id") == operation_id:
            return item
        if operation_type is not None and item.get("operation_type") == operation_type:
            found = item
    return found


def timing_evidence(generation_card: Mapping[str, Any] | None) -> dict[str, Any]:
    """Seconds, retries and flagged slides from the published timing receipt."""

    receipt = ((generation_card or {}).get("result") or {}).get("generation_timing_receipt")
    if not isinstance(receipt, Mapping):
        return {"receipt": None, "totalSeconds": None, "retries": None, "flaggedSlides": None, "fitSummary": None}
    attempts = int(receipt.get("attemptNumber") or 1)
    provider_retries = int((((receipt.get("providerUsage") or {}).get("totals") or {}).get("retryCalls")) or 0)
    return {
        "receipt": {
            "schemaVersion": receipt.get("schemaVersion"),
            "attemptId": receipt.get("attemptId"),
            "outcome": receipt.get("outcome"),
            "validity": receipt.get("validity"),
            "queueWaitMillis": receipt.get("queueWaitMillis"),
            "wallMillis": receipt.get("wallMillis"),
            "stages": receipt.get("stages"),
            "logicalCalls": receipt.get("logicalCalls"),
            "retryReason": receipt.get("retryReason"),
            "terminalError": receipt.get("terminalError"),
        },
        "totalSeconds": None if receipt.get("wallMillis") is None else round(receipt["wallMillis"] / 1000, 2),
        "retries": {"attempts": attempts - 1, "providerRetryCalls": provider_retries,
                    "total": attempts - 1 + provider_retries},
        "flaggedSlides": list(receipt.get("flaggedSlides") or []),
        "fitSummary": list(receipt.get("fitSummary") or []),
    }


def quality_receipts_between(app_data: Path, started: float, finished: float) -> list[dict[str, Any]]:
    """QualityReceipt v2 records sealed while this deck ran.

    The ledger file name is a hash of the job id, which the public API does
    not expose, so the deck is matched by the ledger's write time.  That is
    only unambiguous when the backend runs one bench deck at a time, which is
    how ``run`` drives it and why the bench wants an isolated backend.
    """

    root = app_data / QUALITY_RECEIPT_DIR
    if not root.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("job-*.json")):
        if path.name.endswith("-timing.json") or path.name.endswith("-attempt-timing.json"):
            continue
        mtime = path.stat().st_mtime
        if not started <= mtime <= finished + 5:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for receipt in (payload.get("receipts") or {}).values():
            records.append({"ledger": path.name, **receipt})
    return records


def _v3_blockers(export_receipt: Mapping[str, Any] | None) -> list[str]:
    """Required quality-receipt/v3 dimensions that did not pass."""

    blockers = []
    for name, value in (export_receipt or {}).items():
        if isinstance(value, Mapping) and value.get("required") and value.get("status") == "failed":
            blockers.append(f"{name}:{value.get('reasonCode')}")
    return sorted(blockers)


def gate_evidence(
    quality: list[Mapping[str, Any]],
    timing: Mapping[str, Any],
    generation_card: Mapping[str, Any] | None,
    export_card: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Blockers from the authored QualityReceipt v2 ledger and the export's
    quality-receipt/v3; flags from flagged pages and flagged slides."""

    latest = max(quality, key=lambda r: int(r.get("revision") or 0)) if quality else None
    generation = (generation_card or {}).get("result") or {}
    export_receipt = ((export_card or {}).get("result") or {}).get("quality_receipt")
    pages = generation.get("quality_pages") if isinstance(generation.get("quality_pages"), Mapping) else {}
    flagged_slides = timing.get("flaggedSlides") or []
    blockers = [f"v2:{b}" for b in (latest or {}).get("blockers") or []] + _v3_blockers(export_receipt)
    return {
        "blockers": blockers,
        "flags": {"flaggedPages": pages.get("flagged"), "passedPages": pages.get("passed"),
                  "flaggedSlides": flagged_slides},
        "flagCount": max(int(pages.get("flagged") or 0), len(flagged_slides)),
        "qualityStatus": generation.get("quality_status"),
        "releaseReadiness": (export_receipt or {}).get("releaseReadiness"),
        "v2": None if latest is None else {
            "gatePassed": latest.get("gatePassed"), "qualityStatus": latest.get("qualityStatus"),
            "capacityStatus": latest.get("capacityStatus"), "safetyStatus": latest.get("safetyStatus"),
            "rasterFallbacks": len(latest.get("rasterFallbacks") or []),
            "ledgers": sorted({str(r.get("ledger")) for r in quality}),
        },
        "v3Dimensions": {
            name: {"status": value.get("status"), "required": value.get("required"),
                   "reasonCode": value.get("reasonCode")}
            for name, value in (export_receipt or {}).items()
            if isinstance(value, Mapping) and "status" in value
        },
    }


def source_formats(presentation: Mapping[str, Any]) -> list[str]:
    return sorted({str(s.get("source_format")) for s in presentation.get("slides") or []})


def local_fit_unresolved(timing: Mapping[str, Any], formats: list[str]) -> int | None:
    """Slides whose fit stayed unresolved at publish.

    Local fit is an authored-HTML stage; an svg deck never runs it, so its
    count is ``None`` (not applicable) rather than a flattering zero.  For an
    authored deck the timing receipt lists composition-gate misses
    (``fitSummary``) and slides published under a terminal quality flag
    (``flaggedSlides``); without a timing receipt the count is unmeasured.
    """

    if timing.get("receipt") is None or formats == ["svg"]:
        return None
    indices = {entry.get("slideIndex") for entry in timing.get("fitSummary") or []}
    indices |= {entry.get("index") for entry in timing.get("flaggedSlides") or []}
    return len(indices)


def slide_count(presentation: Mapping[str, Any]) -> int:
    slides = presentation.get("slides")
    return len(slides) if isinstance(slides, list) else int(presentation.get("n_slides") or 0)
