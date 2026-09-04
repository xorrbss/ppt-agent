"""Live-mode submission and polling for the Composer acceptance gate.

Submits real ``source_content_restyle`` operations against a running
backend and polls ``workspace_operations`` to a terminal state, following
the shape proven by a prior session's manual scripts (upload -> snapshot ->
analyze -> create -> bootstrap -> generate/async, then DB polling) rather
than reinventing it. This module is imported and its flag-gated by
``composer_acceptance_gate.py --mode live
--i-understand-this-submits-live-operations``; **this session has not run
it** -- another session owns the live stack for A6/A7 and restyle
operations must not collide with it.

To run it later, from ``servers/fastapi``::

    .venv\\Scripts\\python.exe scripts/composer_acceptance_gate.py --mode live \\
        --manifest path\\to\\deck-manifest.json --env-json path\\to\\env.json \\
        --runs 20 --i-understand-this-submits-live-operations

The deck manifest is a JSON list of ``{"label": str, "pptx": path, "title":
str, "unseen": bool}`` objects; the sweep cycles through it round-robin for
``--runs`` submissions. ``unseen`` marks a deck this gate has not scored
before, for A1's ">= 3 previously unseen" bar -- this harness has no memory
of prior gate runs, so the manifest is the source of truth for that flag.

The env JSON (same format as ``composer_offline_eval.py --env-json``) is
loaded with ``composer_offline_eval_inputs.load_env_json``, which never
prints its values; it must carry ``DATABASE_URL`` and whatever API
credentials the backend needs. Never log, echo or commit that file.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.composer_acceptance_gate_score import RunEvidence, SlideEvidence
from scripts.composer_offline_eval_inputs import load_env_json

TERMINAL_STATES = {"succeeded", "failed", "cancelled", "deadline_exceeded", "dead_letter"}


@dataclass(frozen=True)
class LiveConfig:
    manifest_path: Path
    base_url: str
    env_json: Path | None
    runs: int
    poll_interval_seconds: float
    op_timeout_minutes: float
    out_dir: Path


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        decks = json.load(handle)
    if not isinstance(decks, list) or not decks:
        raise ValueError(f"{path}: expected a non-empty JSON array of deck entries")
    return decks


def _run_curl(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=120)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"unexpected response from {args[2]}: {completed.stdout[:300]}") from error


def _post_json(
    base_url: str, path: str, body: dict[str, Any], *, idempotency_key: str, tmp_dir: Path
) -> dict[str, Any]:
    request_path = tmp_dir / f"req-{uuid.uuid4().hex}.json"
    request_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    args = [
        "curl", "-s", "-X", "POST", f"{base_url}{path}",
        "-H", "Content-Type: application/json; charset=utf-8",
        "-H", f"Idempotency-Key: {idempotency_key}",
        "--data-binary", f"@{request_path}",
    ]
    try:
        return _run_curl(args)
    finally:
        request_path.unlink(missing_ok=True)


def submit_restyle_operation(*, base_url: str, deck: dict[str, Any], tmp_dir: Path) -> tuple[str, str]:
    """Upload the deck, pin its manifest and start one restyle run.

    Mirrors the prior session's ``run_restyle.py``: the asset upload expires,
    so every run re-uploads the same bytes; the analysis is deterministic so
    the pinned manifest describes the same pages every time.
    """

    key = lambda name: f"gate-{name}-{int(time.time())}-{uuid.uuid4().hex[:8]}"  # noqa: E731
    pptx_path = Path(deck["pptx"])
    asset = _run_curl([
        "curl", "-s", "-X", "POST", f"{base_url}/assets/documents",
        "-H", f"Idempotency-Key: {key('up')}",
        "-F", f"file=@{pptx_path};filename=source.pptx;"
              "type=application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ])
    snapshot = _post_json(
        base_url, "/references/snapshots",
        {"provider_id": "uploaded_document", "source": {"asset_ids": [asset["asset_id"]]}},
        idempotency_key=key("snap"), tmp_dir=tmp_dir,
    )
    analysis = _run_curl([
        "curl", "-s", "-X", "POST", f"{base_url}/source-content/v1/analyze",
        "-H", f"Idempotency-Key: {key('an')}",
        "-F", f"referenceSnapshotId={snapshot['snapshot_id']}",
        "-F", "contractVersion=source-content-analysis-request/v1",
    ])
    pin = analysis["manifestPin"]
    presentation_id = str(uuid.uuid4())
    title = deck.get("title") or f"acceptance-gate {deck.get('label', presentation_id[:8])}"
    _post_json(base_url, "/presentation/create", {
        "content": title, "presentation_id": presentation_id, "language": "Korean",
        "reference_snapshot_ids": [snapshot["snapshot_id"]],
    }, idempotency_key=key("create"), tmp_dir=tmp_dir)
    _post_json(
        base_url, f"/workspace/bootstrap?presentation_id={presentation_id}",
        {"idempotency_key": key("boot"), "prompt": title, "asset_ids": [asset["asset_id"]]},
        idempotency_key=key("bootpost"), tmp_dir=tmp_dir,
    )
    started = _post_json(base_url, "/presentation/generate/async", {
        "presentation_id": presentation_id, "content": title, "template": "authored", "strategy": "authored",
        "export_as": "pptx", "vision_qa": False, "authored_style": "default", "language": "Korean",
        "sourceMode": "source_content_restyle", "sourceContentManifestPin": pin,
        "contentPolicy": {
            "text": "verbatim", "preserveAllTableCells": False, "preserveAllChartValues": False,
            "preserveAllImageOccurrences": True, "preserveSourceOrder": True, "preserveSourceSlideCount": True,
            "allowSlideExpansion": False, "allowSummarization": False,
            "overflowPolicy": "same_page_recompose_or_fail",
        },
    }, idempotency_key=key("gen"), tmp_dir=tmp_dir)
    operation_id = started.get("operation_id")
    if not operation_id:
        raise RuntimeError(f"generate/async did not return operation_id: {started}")
    return str(operation_id), presentation_id


async def _poll_operation(
    database_url: str, operation_id: str, *, poll_interval: float, timeout_minutes: float
) -> dict[str, Any]:
    """Poll ``workspace_operations`` (joined to the generation job) to a terminal state."""

    import asyncio

    import asyncpg

    deadline = time.monotonic() + timeout_minutes * 60.0
    url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    while True:
        connection = await asyncpg.connect(url)
        try:
            row = await connection.fetchrow(
                """
                select wo.id as operation_id, wo.state as op_state, wo.presentation_id,
                       p.lifecycle_status, pgj.id as job_id
                  from workspace_operations wo
                  join presentations p on p.id = wo.presentation_id
                  left join presentation_generation_jobs pgj on pgj.workspace_operation_id = wo.id
                 where wo.id = $1
                """,
                uuid.UUID(operation_id),
            )
        finally:
            await connection.close()
        if row is None:
            raise RuntimeError(f"operation {operation_id} not found")
        if row["op_state"] in TERMINAL_STATES:
            return dict(row)
        if time.monotonic() > deadline:
            return {**dict(row), "op_state": "timeout"}
        await asyncio.sleep(poll_interval)


async def _provider_calls_for_job(database_url: str, job_id: uuid.UUID | None) -> int | None:
    if job_id is None:
        return None
    import asyncpg

    url = database_url.replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(url)
    try:
        total = await connection.fetchval(
            "select coalesce(sum(request_count), 0) from provider_usage_receipts where job_id = $1", job_id
        )
    finally:
        await connection.close()
    return int(total)


def _receipt_slides(receipt: dict[str, Any]) -> tuple[SlideEvidence, ...]:
    slides = []
    for row in receipt.get("slides", ()):
        slides.append(SlideEvidence(
            index=row.get("index", 0), rung=row.get("rung"),
            blocking_issues=tuple(row.get("blockingIssues") or ()),
            residual_issues=tuple(row.get("residualIssues") or ()),
        ))
    return tuple(slides)


async def _score_one_run(
    *, deck: dict[str, Any], operation_id: str, presentation_id: str, poll: dict[str, Any]
) -> RunEvidence:
    from services.composer.receipt import composer_receipt_path

    job_id = poll.get("job_id")
    op_status = "published" if poll["op_state"] == "succeeded" and poll["lifecycle_status"] == "published" else (
        "timeout" if poll["op_state"] == "timeout" else "failed"
    )
    receipt: dict[str, Any] | None = None
    receipt_path = composer_receipt_path(job_id, presentation_id) if job_id else None
    if receipt_path is not None and receipt_path.is_file():
        with receipt_path.open(encoding="utf-8") as handle:
            receipt = json.load(handle)
    source_readback = (receipt or {}).get("sourceReadback") or {}
    provider_calls = None
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        provider_calls = await _provider_calls_for_job(database_url, job_id)
    return RunEvidence(
        label=f"{deck.get('label', 'deck')}-{operation_id[:8]}",
        source="live",
        evidence_path=str(receipt_path) if receipt_path else f"workspace_operations:{operation_id}",
        deck_key=str(deck.get("digest") or deck.get("label")),
        unseen=bool(deck.get("unseen", False)),
        page_count=receipt.get("pageCount") if receipt else None,
        wall_ms=(receipt or {}).get("timingsMs", {}).get("total"),
        provider_calls=provider_calls,
        op_status=op_status,
        readback_passed=source_readback.get("passed") if receipt else None,
        checked_block_count=source_readback.get("checkedBlockCount"),
        missing_block_count=len(source_readback.get("missingBlockIds") or ()),
        slides=_receipt_slides(receipt) if receipt else (),
        notes=() if receipt else ("no composer receipt found for this operation",),
    )


async def run_live_sweep(config: LiveConfig) -> tuple[list[RunEvidence], list[str]]:
    """Submit ``config.runs`` restyle operations round-robin over the manifest deck list."""

    if config.env_json and config.env_json.is_file():
        load_env_json(config.env_json)
    decks = _load_manifest(config.manifest_path)
    config.out_dir.mkdir(parents=True, exist_ok=True)
    runs: list[RunEvidence] = []
    problems: list[str] = []
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return [], ["DATABASE_URL not set after loading --env-json; cannot poll operations"]
    for index in range(config.runs):
        deck = decks[index % len(decks)]
        try:
            operation_id, presentation_id = submit_restyle_operation(
                base_url=config.base_url, deck=deck, tmp_dir=config.out_dir
            )
            poll = await _poll_operation(
                database_url, operation_id,
                poll_interval=config.poll_interval_seconds, timeout_minutes=config.op_timeout_minutes,
            )
            runs.append(
                await _score_one_run(
                    deck=deck, operation_id=operation_id, presentation_id=presentation_id, poll=poll
                )
            )
        except Exception as error:
            # A submission or polling failure is itself an A1 data point (an operation that
            # did not reach published), not just a note: keep the sweep going and count it.
            problems.append(f"run {index} ({deck.get('label')}): {type(error).__name__}: {error}")
            runs.append(RunEvidence(
                label=f"{deck.get('label', 'deck')}-run{index}", source="live",
                evidence_path=f"submission-error:run{index}", deck_key=str(deck.get("digest") or deck.get("label")),
                unseen=bool(deck.get("unseen", False)), op_status="failed",
                notes=(f"{type(error).__name__}: {error}",),
            ))
    return runs, problems


__all__ = ["LiveConfig", "run_live_sweep", "submit_restyle_operation"]
