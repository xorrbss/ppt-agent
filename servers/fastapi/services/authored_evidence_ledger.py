"""Attempt-scoped evidence for authored slide capture.

The renderer's PNG and DOM probe caches are intentionally small LRUs.  A quality
review, however, needs a stable record for every slide in an attempt even when a
large deck evicts an item from those caches.  This module is the small adapter at
that boundary: callers hand it the exact HTML, PNG and browser metrics returned by
one capture and receive an immutable :class:`SlideEvidence` value.

The ledger does not perform rendering or quality decisions.  In particular, an
unchanged slide can only be reused through :meth:`reuse_unchanged_slide`; that
operation records zero browser/render captures and makes an accidental final-gate
recollection observable instead of silently replacing the evidence.
"""

from __future__ import annotations

import hashlib
import json
import base64
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_html(html: str) -> str:
    """Return the canonical digest for authored HTML source."""

    if not isinstance(html, str):
        raise TypeError("html must be a string")
    return _sha256(html.encode("utf-8"))


def digest_png(png: bytes) -> str:
    """Return the canonical digest for captured PNG bytes."""

    if not isinstance(png, bytes):
        raise TypeError("png must be bytes")
    return _sha256(png)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def digest_browser_metrics(metrics: Mapping[str, Any] | None) -> str | None:
    """Digest browser telemetry without depending on mapping insertion order."""

    if metrics is None:
        return None
    if not isinstance(metrics, Mapping):
        raise TypeError("browser metrics must be a mapping or None")
    return _sha256(_canonical_json(metrics))


def digest_slide_evidence(
    *,
    attempt_id: str,
    slide_index: int,
    revision: int,
    html_digest: str,
    png_digest: str,
    metrics_digest: str | None,
    capture_policy: str,
    checkpoint_revision: int = 0,
) -> str:
    """Build a stable identity for one evidence record."""

    payload = {
        "attemptId": str(attempt_id),
        "slideIndex": int(slide_index),
        "revision": int(revision),
        "htmlDigest": html_digest,
        "pngDigest": png_digest,
        "metricsDigest": metrics_digest,
        "capturePolicy": capture_policy,
        "checkpointRevision": int(checkpoint_revision),
    }
    return _sha256(_canonical_json(payload))


@dataclass(frozen=True, slots=True)
class SlideEvidence:
    """Immutable HTML/PNG/DOM telemetry tuple captured for one slide.

    ``browser_capture_count`` and ``render_capture_count`` are explicit counters,
    rather than inferred from cache hits.  A reused unchanged record therefore
    carries the original payload while proving that the final gate did not invoke
    Chrome again.
    """

    attempt_id: str
    slide_index: int
    revision: int
    html: str
    png: bytes
    browser_metrics: Mapping[str, Any] | None
    html_digest: str
    png_digest: str
    metrics_digest: str | None
    evidence_digest: str
    capture_policy: str = "captured"
    browser_capture_count: int = 1
    render_capture_count: int = 1
    reused_unchanged: bool = False
    checkpoint_revision: int = 0

    @classmethod
    def captured(
        cls,
        *,
        attempt_id: str,
        slide_index: int,
        revision: int,
        html: str,
        png: bytes,
        browser_metrics: Mapping[str, Any] | None,
        checkpoint_revision: int = 0,
    ) -> "SlideEvidence":
        html_digest = digest_html(html)
        png_digest = digest_png(png)
        metrics_digest = digest_browser_metrics(browser_metrics)
        policy = "captured"
        return cls(
            attempt_id=str(attempt_id),
            slide_index=int(slide_index),
            revision=int(revision),
            html=html,
            png=png,
            browser_metrics=_freeze(browser_metrics),
            html_digest=html_digest,
            png_digest=png_digest,
            metrics_digest=metrics_digest,
            evidence_digest=digest_slide_evidence(
                attempt_id=str(attempt_id),
                slide_index=slide_index,
                revision=revision,
                html_digest=html_digest,
                png_digest=png_digest,
                metrics_digest=metrics_digest,
                capture_policy=policy,
                checkpoint_revision=checkpoint_revision,
            ),
            checkpoint_revision=int(checkpoint_revision),
        )

    @classmethod
    def hydrated(
        cls,
        *,
        attempt_id: str,
        slide_index: int,
        revision: int,
        html: str,
        png: bytes,
        browser_metrics: Mapping[str, Any] | None,
        checkpoint_revision: int = 0,
    ) -> "SlideEvidence":
        """Reconstitute durable checkpoint evidence without claiming a capture."""

        html_digest = digest_html(html)
        png_digest = digest_png(png)
        metrics_digest = digest_browser_metrics(browser_metrics)
        policy = "hydrated"
        return cls(
            attempt_id=str(attempt_id), slide_index=int(slide_index), revision=int(revision),
            html=html, png=png, browser_metrics=_freeze(browser_metrics),
            html_digest=html_digest, png_digest=png_digest, metrics_digest=metrics_digest,
            evidence_digest=digest_slide_evidence(
                attempt_id=str(attempt_id), slide_index=slide_index, revision=revision,
                html_digest=html_digest, png_digest=png_digest,
                metrics_digest=metrics_digest, capture_policy=policy,
                checkpoint_revision=checkpoint_revision,
            ),
            capture_policy=policy, browser_capture_count=0, render_capture_count=0,
            reused_unchanged=False, checkpoint_revision=int(checkpoint_revision),
        )

    def as_dict(self, *, include_payload: bool = False) -> dict[str, Any]:
        """Return a JSON-safe receipt projection.

        Payload bytes are omitted by default because receipts normally carry only
        identities; the in-memory ledger still retains both payloads.
        """

        result: dict[str, Any] = {
            "attemptId": self.attempt_id,
            "slideIndex": self.slide_index,
            "revision": self.revision,
            "htmlDigest": self.html_digest,
            "pngDigest": self.png_digest,
            "metricsDigest": self.metrics_digest,
            "evidenceDigest": self.evidence_digest,
            "capturePolicy": self.capture_policy,
            "browserCaptureCount": self.browser_capture_count,
            "renderCaptureCount": self.render_capture_count,
            "reusedUnchanged": self.reused_unchanged,
            "checkpointRevision": self.checkpoint_revision,
            "browserMetricsCaptured": self.browser_metrics is not None,
        }
        if include_payload:
            result.update({
                "html": self.html,
                "pngBase64": base64.b64encode(self.png).decode("ascii"),
                "browserMetrics": _json_value(self.browser_metrics),
            })
        return result


class EvidenceReuseError(ValueError):
    """Raised when an unchanged slide has no matching retained evidence."""


class EvidenceLedgerSealedError(RuntimeError):
    """Raised when a sealed attempt is mutated."""


class DeckEvidenceLedger:
    """Retain immutable slide evidence independently of renderer cache eviction.

    The default retention policy is deliberately unbounded for attempts and slides;
    callers may explicitly retire completed attempts with :meth:`retire_attempt`.
    This means a 33+ slide deck keeps all evidence even though ``slide_capture``
    evicts entries after its 32-item render/metrics LRU limit.
    """

    def __init__(
        self,
        *,
        attempt_id: str | None = None,
        retention_ttl_seconds: float | None = None,
        retention_byte_budget: int | None = None,
    ) -> None:
        self._lock = RLock()
        self._attempts: "OrderedDict[str, OrderedDict[int, list[SlideEvidence]]]" = OrderedDict()
        self._sealed: set[str] = set()
        self._attempt_revisions: dict[str, int] = {}
        self._attempt_stats: dict[str, dict[str, float | int | None]] = {}
        # Payloads are immutable and unchanged/retry revisions often point at
        # the exact same HTML/PNG/metrics objects.  Track digest identities so
        # retention byte accounting reflects resident memory rather than
        # multiplying the same payload for every revision in the history.
        self._attempt_payload_digests: dict[str, set[str]] = {}
        self.retention_ttl_seconds = max(0.0, float(
            os.getenv("AUTHORED_EVIDENCE_RETENTION_TTL_SECONDS", "86400")
            if retention_ttl_seconds is None else retention_ttl_seconds
        ))
        self.retention_byte_budget = max(0, int(
            os.getenv("AUTHORED_EVIDENCE_RETENTION_BYTE_BUDGET", str(512 * 1024 * 1024))
            if retention_byte_budget is None else retention_byte_budget
        ))
        # Receipt construction is owned by the unified evaluator.  Storing its
        # cache here prevents separate final-gate owners creating two receipts.
        self.receipt_store: dict[tuple[str, int], Any] = {}
        if attempt_id is not None:
            self.begin_attempt(attempt_id)

    def begin_attempt(self, attempt_id: str) -> str:
        attempt = str(attempt_id)
        if not attempt:
            raise ValueError("attempt_id must not be empty")
        with self._lock:
            self._attempts.setdefault(attempt, OrderedDict())
            self._attempt_revisions.setdefault(attempt, 0)
            self._attempt_payload_digests.setdefault(attempt, set())
            self._attempt_stats.setdefault(
                attempt, {"created": time.time(), "touched": time.time(), "sealed": None, "bytes": 0}
            )
        return attempt

    def _require_attempt(self, attempt_id: str) -> str:
        attempt = str(attempt_id)
        if attempt not in self._attempts:
            self.begin_attempt(attempt)
        if attempt in self._sealed:
            raise EvidenceLedgerSealedError(f"attempt {attempt} is sealed")
        return attempt

    def _next_revision(self, attempt: str, requested: int | None) -> int:
        current = self._attempt_revisions.get(attempt, 0)
        revision = current + 1 if requested is None else int(requested)
        if revision <= current:
            raise ValueError(f"revision must increase for attempt {attempt}")
        self._attempt_revisions[attempt] = revision
        return revision

    def record_slide(
        self,
        *,
        attempt_id: str,
        slide_index: int,
        html: str,
        png: bytes = b"",
        browser_metrics: Mapping[str, Any] | None = None,
        revision: int | None = None,
        unchanged: bool = False,
        checkpoint_revision: int = 0,
    ) -> SlideEvidence:
        """Record one fresh capture, or route ``unchanged`` to the reuse guard."""

        if unchanged:
            if png or browser_metrics is not None:
                raise EvidenceReuseError("unchanged slide cannot provide a new PNG or metrics payload")
            return self.reuse_unchanged_slide(
                attempt_id=attempt_id,
                slide_index=slide_index,
                html=html,
                revision=revision,
            )
        with self._lock:
            attempt = self._require_attempt(attempt_id)
            evidence = SlideEvidence.captured(
                attempt_id=attempt,
                slide_index=slide_index,
                revision=self._next_revision(attempt, revision),
                html=html,
                png=png,
                browser_metrics=browser_metrics,
                checkpoint_revision=checkpoint_revision,
            )
            self._attempts[attempt].setdefault(int(slide_index), []).append(evidence)
            self._touch_attempt(attempt, evidence)
            self.sweep_retention()
            return evidence

    def reuse_unchanged_slide(
        self,
        *,
        attempt_id: str,
        slide_index: int,
        html: str | None = None,
        html_digest: str | None = None,
        revision: int | None = None,
        source_attempt_id: str | None = None,
    ) -> SlideEvidence:
        """Reuse retained evidence without browser or render work.

        A digest (or exact HTML) must match the latest record for the slide.  The
        returned record has a new ledger revision and zero capture counters, making
        unchanged reuse explicit in downstream gate/receipt projections.
        """

        with self._lock:
            attempt = self._require_attempt(attempt_id)
            history = self._attempts[attempt].get(int(slide_index))
            previous: SlideEvidence | None = history[-1] if history else None
            if not history and source_attempt_id is not None:
                source_history = self._attempts.get(str(source_attempt_id), {}).get(int(slide_index))
                if source_history:
                    # Copy the retained payload into the new attempt before
                    # creating the zero-capture reuse record.  Retries therefore
                    # cannot fall back to an evicted renderer cache or defective
                    # source HTML from an earlier stage.
                    previous = replace(source_history[-1], attempt_id=attempt)
            if previous is None:
                raise EvidenceReuseError("no retained evidence for unchanged slide")
            expected = html_digest or (digest_html(html) if html is not None else previous.html_digest)
            if expected != previous.html_digest:
                raise EvidenceReuseError("unchanged slide HTML digest does not match retained evidence")
            next_revision = self._next_revision(attempt, revision)
            reused = replace(
                previous,
                revision=next_revision,
                evidence_digest=digest_slide_evidence(
                    attempt_id=attempt,
                    slide_index=slide_index,
                    revision=next_revision,
                    html_digest=previous.html_digest,
                    png_digest=previous.png_digest,
                    metrics_digest=previous.metrics_digest,
                    capture_policy="reused_unchanged",
                    checkpoint_revision=previous.checkpoint_revision,
                ),
                capture_policy="reused_unchanged",
                browser_capture_count=0,
                render_capture_count=0,
                reused_unchanged=True,
                checkpoint_revision=previous.checkpoint_revision,
            )
            self._attempts[attempt].setdefault(int(slide_index), []).append(reused)
            self._touch_attempt(attempt, reused)
            self.sweep_retention()
            return reused

    # Alias used by callers that phrase the operation as a cache lookup.
    reuse_unchanged = reuse_unchanged_slide

    # Narrow aliases keep the adapter easy to adopt at existing capture call
    # sites while preserving one implementation and one retention boundary.
    add_slide = record_slide

    def hydrate_slide(
        self,
        *,
        attempt_id: str,
        slide_index: int,
        html: str,
        png: bytes,
        browser_metrics: Mapping[str, Any] | None,
        checkpoint_revision: int = 0,
        revision: int | None = None,
    ) -> SlideEvidence:
        """Load an existing checkpoint bundle without browser/render work."""

        with self._lock:
            attempt = self._require_attempt(attempt_id)
            latest = self._attempts[attempt].get(int(slide_index), [])
            if latest:
                previous = latest[-1]
                if previous.html_digest == digest_html(html) and previous.png_digest == digest_png(png):
                    return previous
            evidence = SlideEvidence.hydrated(
                attempt_id=attempt, slide_index=slide_index,
                revision=self._next_revision(attempt, revision), html=html,
                png=png, browser_metrics=browser_metrics,
                checkpoint_revision=checkpoint_revision,
            )
            self._attempts[attempt].setdefault(int(slide_index), []).append(evidence)
            self._touch_attempt(attempt, evidence)
            self.sweep_retention()
            return evidence

    @staticmethod
    def _evidence_size(evidence: SlideEvidence) -> int:
        try:
            metrics = len(_canonical_json(evidence.browser_metrics)) if evidence.browser_metrics is not None else 0
        except Exception:
            metrics = 0
        return len(evidence.html.encode("utf-8")) + len(evidence.png) + metrics

    def _touch_attempt(self, attempt: str, evidence: SlideEvidence) -> None:
        stats = self._attempt_stats.setdefault(
            attempt, {"created": time.time(), "touched": time.time(), "sealed": None, "bytes": 0}
        )
        stats["touched"] = time.time()
        # A reused unchanged revision retains the same immutable payload.  Count
        # each digest once so byte-budget eviction remains stable under retries
        # and does not evict a sealed attempt merely because its history grew.
        seen = self._attempt_payload_digests.setdefault(attempt, set())
        payloads = (
            ("html:", evidence.html_digest, len(evidence.html.encode("utf-8"))),
            ("png:", evidence.png_digest, len(evidence.png)),
        )
        if evidence.metrics_digest is not None:
            try:
                metric_size = len(_canonical_json(evidence.browser_metrics))
            except Exception:
                metric_size = 0
            payloads += (("metrics:", evidence.metrics_digest, metric_size),)
        incremental = 0
        for prefix, digest, size in payloads:
            key = prefix + digest
            if key not in seen:
                seen.add(key)
                incremental += size
        stats["bytes"] = int(stats.get("bytes") or 0) + incremental

    def latest(self, attempt_id: str, slide_index: int) -> SlideEvidence | None:
        with self._lock:
            history = self._attempts.get(str(attempt_id), {}).get(int(slide_index))
            return history[-1] if history else None

    def history(self, attempt_id: str, slide_index: int) -> tuple[SlideEvidence, ...]:
        with self._lock:
            return tuple(self._attempts.get(str(attempt_id), {}).get(int(slide_index), ()))

    def slides(self, attempt_id: str) -> tuple[SlideEvidence, ...]:
        with self._lock:
            records = self._attempts.get(str(attempt_id), {})
            return tuple(records[index][-1] for index in sorted(records) if records[index])

    def can_reuse_unchanged(
        self, *, attempt_id: str, slide_index: int, html_digest: str
    ) -> bool:
        latest = self.latest(attempt_id, slide_index)
        return latest is not None and latest.html_digest == html_digest

    def should_recapture(
        self, *, attempt_id: str, slide_index: int, html_digest: str, unchanged: bool = False
    ) -> bool:
        """Return whether capture work is needed for a slide.

        Final gates should pass ``unchanged=True`` and branch to reuse when this
        returns ``False``; the method is metadata only and never invokes Chrome.
        """

        return not unchanged or not self.can_reuse_unchanged(
            attempt_id=attempt_id, slide_index=slide_index, html_digest=html_digest
        )

    def deck_digest(self, attempt_id: str) -> str:
        records = [item.evidence_digest for item in self.slides(attempt_id)]
        return _sha256(_canonical_json(records))

    def deck_html_digest(self, attempt_id: str) -> str:
        """Order-sensitive digest of every slide's exact HTML identity."""

        return _sha256(_canonical_json([item.html_digest for item in self.slides(attempt_id)]))

    def revision(self, attempt_id: str) -> int:
        with self._lock:
            return self._attempt_revisions.get(str(attempt_id), 0)

    def ledger_digest(self, attempt_id: str | None = None) -> str:
        with self._lock:
            payload = {
                attempt: [evidence.evidence_digest for index in sorted(slides) for evidence in slides[index]]
                for attempt, slides in self._attempts.items()
                if attempt_id is None or attempt == str(attempt_id)
            }
        return _sha256(_canonical_json(payload))

    def as_dict(self, attempt_id: str, *, include_payload: bool = False) -> dict[str, Any]:
        """Stable quality-receipt input for an attempt, without cache dependence."""

        with self._lock:
            return {
                "schemaVersion": "DeckEvidenceLedger/v1",
                "attemptId": str(attempt_id),
                "revision": self.revision(attempt_id),
                "ledgerDigest": self.ledger_digest(attempt_id),
                "deckHtmlDigest": self.deck_html_digest(attempt_id),
                "slides": [item.as_dict(include_payload=include_payload) for item in self.slides(attempt_id)],
                "sealed": str(attempt_id) in self._sealed,
            }

    def seal_attempt(self, attempt_id: str) -> str:
        attempt = str(attempt_id)
        with self._lock:
            if attempt not in self._attempts:
                raise EvidenceReuseError("cannot seal unknown attempt")
            self._sealed.add(attempt)
            self._attempt_stats.setdefault(attempt, {"created": time.time(), "touched": time.time(), "sealed": None, "bytes": 0})["sealed"] = time.time()
            sealed_digest = self.deck_digest(attempt)
            self.sweep_retention()
            return sealed_digest

    def sweep_retention(self, *, now: float | None = None, force: bool = False) -> tuple[str, ...]:
        """Retire only sealed attempts after TTL or byte pressure.

        Active attempts are never evicted.  This guarantees all HTML/PNG/metrics
        remain available until ``seal_attempt`` (receipt seal); completed attempts
        become reclaimable by TTL or oldest first when the byte budget is exceeded.
        """
        now = time.time() if now is None else float(now)
        with self._lock:
            candidates: list[tuple[str, float, int]] = []
            total = 0
            for attempt, stats in self._attempt_stats.items():
                size = int(stats.get("bytes") or 0)
                total += size
                sealed = stats.get("sealed")
                if sealed is not None:
                    candidates.append((attempt, float(sealed), size))
            evict: set[str] = set()
            for attempt, sealed, _ in candidates:
                if force or self.retention_ttl_seconds == 0 or now - sealed >= self.retention_ttl_seconds:
                    evict.add(attempt)
            if self.retention_byte_budget >= 0 and total > self.retention_byte_budget:
                for attempt, _, size in sorted(candidates, key=lambda item: item[1]):
                    if attempt in evict:
                        total -= size
                    elif total > self.retention_byte_budget:
                        evict.add(attempt)
                        total -= size
            for attempt in evict:
                self._attempts.pop(attempt, None)
                self._attempt_revisions.pop(attempt, None)
                self._attempt_stats.pop(attempt, None)
                self._attempt_payload_digests.pop(attempt, None)
                self._sealed.discard(attempt)
                for key in tuple(self.receipt_store):
                    if key[0] == attempt:
                        self.receipt_store.pop(key, None)
            return tuple(sorted(evict))

    def retention_stats(self, attempt_id: str | None = None) -> dict[str, Any]:
        """Expose byte/age state for large deck performance evidence."""
        with self._lock:
            attempts = self._attempt_stats if attempt_id is None else {str(attempt_id): self._attempt_stats.get(str(attempt_id), {})}
            return {key: dict(value) for key, value in attempts.items()}

    def retire_attempt(self, attempt_id: str) -> None:
        """Explicitly release a completed attempt; never called by cache eviction."""

        with self._lock:
            self._attempts.pop(str(attempt_id), None)
            self._attempt_revisions.pop(str(attempt_id), None)
            self._attempt_stats.pop(str(attempt_id), None)
            self._attempt_payload_digests.pop(str(attempt_id), None)
            self._sealed.discard(str(attempt_id))
            for key in tuple(self.receipt_store):
                if key[0] == str(attempt_id):
                    self.receipt_store.pop(key, None)


async def capture_slide_evidence(
    ledger: DeckEvidenceLedger,
    *,
    attempt_id: str,
    slide_index: int,
    html: str,
    timeout: int = 60,
    revision: int | None = None,
) -> SlideEvidence:
    """Capture through ``slide_capture`` and atomically retain its tuple."""

    from utils.slide_capture import render_html_to_png_with_metrics

    capture = await render_html_to_png_with_metrics(html, timeout=timeout)
    # RenderCapture is atomic; tolerate legacy tuple adapters used by older
    # integrations while preferring the explicit payload fields.
    if hasattr(capture, "png") and hasattr(capture, "browser_metrics"):
        png, metrics = capture.png, capture.browser_metrics
    else:
        png, metrics = capture
    return ledger.record_slide(
        attempt_id=attempt_id,
        slide_index=slide_index,
        html=html,
        png=png,
        browser_metrics=metrics,
        revision=revision,
    )


__all__ = [
    "DeckEvidenceLedger",
    "EvidenceLedgerSealedError",
    "EvidenceReuseError",
    "SlideEvidence",
    "capture_slide_evidence",
    "digest_browser_metrics",
    "digest_html",
    "digest_png",
    "digest_slide_evidence",
]
