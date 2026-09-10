"""Cached baseline-to-candidate style comparison for authored VQA."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
from typing import List, Optional

from utils.llm_calls.critique_slide import SlideCritique, StyleComparisonCritique


def _runtime():
    return importlib.import_module("utils.llm_calls.author_vision_qa")

def _style_comparison_cache_key(
    baseline: bytes,
    candidate: bytes,
    context: Optional[str],
    *,
    reference_mode: str = _runtime().STYLE_REFERENCE,
) -> str:
    """Fingerprint a style comparison independently from all existing QA caches."""
    mode = _runtime().normalize_reference_mode(reference_mode)
    if mode != _runtime().STYLE_REFERENCE:
        raise ValueError("Authored style comparison only supports style_reference mode")

    digest = hashlib.sha256()
    digest.update(_runtime().STYLE_COMPARISON_VQA_PROMPT_VERSION.encode("utf-8"))
    digest.update(b"\0")
    try:
        model = str(_runtime().get_model() or "")
    except Exception:
        model = ""
    digest.update(model.encode("utf-8"))
    digest.update(b"\0")
    digest.update(mode.encode("utf-8"))
    digest.update(b"\0")
    digest.update((context or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(hashlib.sha256(baseline).digest())
    digest.update(hashlib.sha256(candidate).digest())
    return digest.hexdigest()

async def _style_comparison_cached(
    baseline: bytes,
    candidate: bytes,
    context: Optional[str],
    *,
    reference_mode: str = _runtime().STYLE_REFERENCE,
) -> Optional[SlideCritique]:
    """Return one best-effort baseline-vs-candidate style critique."""
    mode = _runtime().normalize_reference_mode(reference_mode)
    if mode != _runtime().STYLE_REFERENCE:
        raise ValueError("Authored style comparison only supports style_reference mode")

    key = _style_comparison_cache_key(
        baseline,
        candidate,
        context,
        reference_mode=mode,
    )

    def record_failure(error: Exception) -> None:
        _runtime()._style_comparison_vqa_failures[key] = {
            "error_type": type(error).__name__,
            "reason_code": str(getattr(error, "code", "vqa.provider_failed")),
            "status_code": getattr(error, "status_code", None),
        }
        _runtime()._style_comparison_vqa_failures.move_to_end(key)
        limit = max(1, _runtime().VQA_CACHE_SIZE)
        while len(_runtime()._style_comparison_vqa_failures) > limit:
            _runtime()._style_comparison_vqa_failures.popitem(last=False)

    async def invoke() -> SlideCritique:
        async with _runtime()._get_vqa_sem():
            return await _runtime().critique_slide_style_comparison(
                baseline,
                candidate,
                slide_context=context,
            )

    if _runtime().VQA_CACHE_SIZE <= 0:
        try:
            critique = await invoke()
            _runtime()._style_comparison_vqa_failures.pop(key, None)
            return critique
        except Exception as error:
            record_failure(error)
            return None
    cached = _runtime()._style_comparison_vqa_cache.get(key)
    if cached is not None:
        _runtime()._style_comparison_vqa_cache.move_to_end(key)
        return cached

    task = _runtime()._style_comparison_vqa_inflight.get(key)
    if task is None:
        task = asyncio.create_task(invoke())
        _runtime()._style_comparison_vqa_inflight[key] = task

    try:
        critique = await asyncio.shield(task)
    except Exception as error:
        record_failure(error)
        return None
    finally:
        if _runtime()._style_comparison_vqa_inflight.get(key) is task and task.done():
            _runtime()._style_comparison_vqa_inflight.pop(key, None)

    _runtime()._style_comparison_vqa_cache[key] = critique
    _runtime()._style_comparison_vqa_failures.pop(key, None)
    _runtime()._style_comparison_vqa_cache.move_to_end(key)
    while len(_runtime()._style_comparison_vqa_cache) > _runtime().VQA_CACHE_SIZE:
        _runtime()._style_comparison_vqa_cache.popitem(last=False)
    return critique

def style_comparison_failure_diagnostic(
    baseline: bytes,
    candidate: bytes,
    context: Optional[str],
    *,
    reference_mode: str = _runtime().STYLE_REFERENCE,
) -> dict[str, object] | None:
    """Return bounded provider failure evidence for one exact paired request."""

    key = _style_comparison_cache_key(
        baseline,
        candidate,
        context,
        reference_mode=_runtime().normalize_reference_mode(reference_mode),
    )
    value = _runtime()._style_comparison_vqa_failures.get(key)
    return dict(value) if value is not None else None

async def critique_authored_style_comparison(
    baseline_pngs: List[bytes],
    candidate_pngs: List[bytes],
    contexts: Optional[List[Optional[str]]] = None,
    *,
    reference_mode: str = _runtime().STYLE_REFERENCE,
) -> List[Optional[StyleComparisonCritique]]:
    """Compare post-fix authored renders with non-authoritative pre-fix baselines.

    Returns ``None`` for a candidate whose single model call failed. Empty batches are
    valid and return immediately without invoking the model.
    """
    mode = _runtime().normalize_reference_mode(reference_mode)
    if mode != _runtime().STYLE_REFERENCE:
        raise ValueError("Authored style comparison only supports style_reference mode")
    if len(baseline_pngs) != len(candidate_pngs):
        raise ValueError("Style comparison baseline and candidate counts must match")
    if any(not isinstance(image, bytes) or not image for image in baseline_pngs):
        raise ValueError("Style comparison baselines must be non-empty bytes")
    if any(not isinstance(image, bytes) or not image for image in candidate_pngs):
        raise ValueError("Style comparison candidates must be non-empty bytes")

    ctx = contexts or []

    async def review(i: int) -> Optional[StyleComparisonCritique]:
        context = ctx[i] if i < len(ctx) else None
        return await _style_comparison_cached(
            baseline_pngs[i],
            candidate_pngs[i],
            context,
            reference_mode=mode,
        )

    return list(await asyncio.gather(*[review(i) for i in range(len(candidate_pngs))]))
