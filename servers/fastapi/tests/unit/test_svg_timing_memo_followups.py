"""F6: optional timing stays diagnostic and manifest memo work stays independent."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from dataclasses import replace
import json
import pickle
from threading import Barrier
from types import SimpleNamespace
import uuid

import pytest

from services import authored_generation_timing_receipt as receipts, svg_source_layout as layout
from services.generation_core.authoring import author_slide_svg as author
from services.generation_core.drivers.svg import timing
from services.workspace_generation_timing_payload import (
    generation_timing_result_projection, sanitize_generation_timing_receipt,
    sanitize_generation_timing_result,
)
from tests.unit.test_author_slide_svg_asset_offer import _art_direction_style
from services.presentation_generation_attempt_timing import _persist_terminal_attempt_timing
from services.svg_recovery_witness import author_witness
from services.svg_partial_recovery import _page_render_result_digest
from tests.unit.test_svg_generation_timing_receipt import _context, _request, _seal_success, _usage
from tests.unit.test_svg_source_layout import manifest_for
from utils.hashing import canonical_json_sha256, sha256_hex
from utils.llm_calls.author_prompt_split import author_messages


@pytest.fixture(autouse=True)
def receipt_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path))


@pytest.mark.parametrize("invalid", ["open", "missing_end", "missing_lag", "invalid_lag", "invalid_stage"])
def test_terminal_receipt_drops_invalid_or_open_diagnostics(invalid):
    request = _request()
    recorder = request._authored_generation_timing_recorder
    for name in ("outline", "author", "render", "quality", "publication"):
        recorder.record_stage_ms(name, 0)
    detail = timing.RenderAttemptObservations(request, page_index=0, attempt_ordinal=1)
    detail.receipt["eventLoopLagMillis"] = 0
    if invalid != "open":
        detail.move(None)
    if invalid == "missing_end":
        detail.receipt["stages"][0].pop("endMillis")
    elif invalid == "missing_lag":
        detail.receipt.pop("eventLoopLagMillis")
    elif invalid == "invalid_lag":
        detail.receipt["eventLoopLagMillis"] = float("nan")
    elif invalid == "invalid_stage":
        detail.receipt["stages"][0]["name"] = "unknown"
    sealed = _seal_success(request, logical_calls=0)
    assert sealed is not None and sealed["validity"] == "valid"
    assert "renderAttempts" not in sealed
    assert sealed["renderAttemptsDropped"] == 1
    assert recorder.render_attempts() == ()
    assert recorder.render_attempts_dropped == 1
    if invalid == "open":
        detail.move(None)
        assert len(recorder.render_attempts()) == 1
        assert recorder.render_attempts_dropped == 0


def test_prepare_relative_clocks_and_non_yielding_lag_probe(monkeypatch):
    now = [500001.0]
    monkeypatch.setattr(timing, "time", SimpleNamespace(monotonic=lambda: now[0]))
    recorder = receipts.GenerationTimingRecorder(started_at=500000.0)
    request = SimpleNamespace(_authored_generation_timing_recorder=recorder)

    async def run():
        detail = timing.RenderAttemptObservations(request, page_index=0, attempt_ordinal=1)
        assert detail.sample_loop_lag() is None
        assert "eventLoopLagMillis" not in detail.receipt
        now[0] += .25
        detail.move("wait")
        now[0] += .25
        detail.move("work")
        now[0] += .25
        detail.move("post")
        now[0] += .25
        detail.move(None)
        # The probe adds no yield/cancellation point before the real await.
        assert recorder.render_attempts() == ()
        await asyncio.sleep(0)
        return recorder.render_attempts()[0]

    value = asyncio.run(run())
    assert value["schema"] == "svg-render-attempt-timing/v2"
    assert value["stages"][0] == {"name": "prepare", "startMillis": 1000,
        "endMillis": 1250, "durationMillis": 250}
    assert recorder.observations()["render.prepare"]["durationMillis"] == 250
    assert recorder.observations()["render.post"]["durationMillis"] == 250
    assert all("Monotonic" not in key for stage in value["stages"] for key in stage)
    assert value["eventLoopLagMillis"] == 1000


def test_public_projection_omits_detail_without_changing_internal_receipt_or_digest():
    request = _request()
    recorder = request._authored_generation_timing_recorder
    detail = timing.RenderAttemptObservations(request, page_index=0, attempt_ordinal=1)
    detail.receipt["eventLoopLagMillis"] = 0
    detail.move(None)
    job_id = uuid.UUID(int=17)
    sealed = asyncio.run(_persist_terminal_attempt_timing(job_id=job_id,
        context=_context("internal-reader"), recorder=recorder, provider_usage=_usage(0, 0),
        queue_wait_ms=0, outcome="succeeded", retry_reason=None, terminal_error=None, request=request))
    parsed = receipts._attempt_receipt_v2_from_dict(sealed)
    assert len(parsed.render_attempts) == 1
    assert receipts._attempt_receipt_v2_digest(replace(parsed, render_attempts=())) == sealed["receiptDigest"]
    for public in (sanitize_generation_timing_receipt(sealed),
                   generation_timing_result_projection(sealed)["generation_timing_receipt"],
                   sanitize_generation_timing_result({
                       "generation_timing_receipt": sealed})["generation_timing_receipt"]):
        assert "renderAttempts" not in public
        assert public["receiptDigest"] == sealed["receiptDigest"]
    assert "renderAttempts" in sealed
    loaded = receipts.load_generation_attempt_timing_receipts(job_id)
    assert loaded[-1].render_attempts == parsed.render_attempts


@pytest.mark.parametrize("changed", ["model", "max_tokens", "prompt_cache_key"])
def test_prefix_settings_digest_covers_cache_partition_settings(changed):
    style = _art_direction_style()
    design = author.svg_design_text(style)
    messages = author_messages(author.svg_author_rules(style, 18), design + "\n\npage", design)
    kwargs = {"model": "m", "max_tokens": 1000, "prompt_cache_key": "cache"}
    before = author.svg_author_input_receipt(style, messages, provider="p", model="m", budget=1000, kwargs=kwargs)
    after = author.svg_author_input_receipt(style, messages, provider="p", model="m", budget=1000,
        kwargs={**kwargs, changed: 2000 if changed == "max_tokens" else "different"})
    assert before["prefix_digest"] == after["prefix_digest"]
    assert before["prefix_cache_settings_digest"] != after["prefix_cache_settings_digest"]


def test_legacy_checkpoint_without_prefix_settings_keeps_stored_witnesses():
    # Persisted digest values were captured from d9bba77c5's digest functions.
    payload = json.loads(json.dumps({"schema_version": "svg-page/v1", "svg_content": "<svg/>",
        "input_digest": "legacy-input", "png_sha256": "legacy-png", "png_path": "legacy.png",
        "quality": {"passed": True, "geometry_passed": True, "measurement_complete": True},
        "attempts": [{"kind": "provider", "raw_response": "<svg/>", "receipt": {
            "provider": "openai", "requested_model": "legacy-model",
            "author_prompt_digest": "sha256:" + "a" * 64, "prefix_digest": "sha256:" + "b" * 64,
            "raw_digest": sha256_hex(b"<svg/>"), "source_digest": sha256_hex(b"<svg/>")}}]}))
    assert "prefix_cache_settings_digest" not in payload["attempts"][0]["receipt"]
    assert author_witness(payload) == "14734e4661d723897798824375d9d89e245bfb74d0a70064fe0fbee72465b233"
    assert _page_render_result_digest(payload) == "6473621aa97a5c23b30d7906d6a8da56939a8da8c98d543417a50896a2ee6bf4"
    assert canonical_json_sha256(payload, allow_nan=False) == (
        "a2793f23b6f77f3712edc3957bf854ccd5778dda291e2970902c283b2a31aa2d")


@pytest.mark.parametrize("container", ["dict", "list"])
def test_frozen_containers_copy_and_pickle_as_plain_containers(container):
    manifest = manifest_for([("table", {"rows": [[{"text": "original"}]]})])
    payload = layout.source_pages(manifest)[0].blocks[0].payload
    value = payload if container == "dict" else payload["rows"]
    expected_type = dict if container == "dict" else list
    for copied in (copy(value), deepcopy(value), pickle.loads(pickle.dumps(value))):
        assert type(copied) is expected_type
        assert copied == value
        if container == "dict":
            copied["extra"] = "editable"
        else:
            copied.append("editable")
    restored = pickle.loads(pickle.dumps(payload))
    restored["rows"][0][0]["text"] = "editable"
    assert payload["rows"][0][0]["text"] == "original"


def test_unrelated_manifest_validations_do_not_hold_global_memo_lock(monkeypatch):
    manifests = [manifest_for([("text", {"text": text})]) for text in ("one", "two")]
    barrier = Barrier(2)
    original = layout._validate_source_pages

    def validate(*args, **kwargs):
        barrier.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(layout, "_validate_source_pages", validate)
    with ThreadPoolExecutor(max_workers=2) as executor:
        pages = list(executor.map(layout.source_pages, manifests))
    assert all(value is manifest.units for value, manifest in zip(pages, manifests, strict=True))
