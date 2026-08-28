from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from services.authored_slide_checkpoint_bundle import (
    AtomicSlideCheckpointStore,
    SlideCheckpointConflict,
    SlideCheckpointCorrupt,
)
from services.authored_checkpoint_artifacts import (
    atomic_write,
    read_valid_evidence,
    render_artifact_path,
    write_valid_evidence,
)
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
import uuid


def test_commit_recover_retains_complete_attempt_revisions(tmp_path: Path):
    store = AtomicSlideCheckpointStore(tmp_path)
    first = store.commit(
        attempt_id="attempt-a",
        slide_index=2,
        revision=0,
        html="<h1>old</h1>",
        png=b"png-old",
        metrics={"title": {"text": "old", "visible": True}},
    )
    repaired = store.commit_repair(
        attempt_id="attempt-a",
        slide_index=2,
        base_revision=0,
        html="<h1>new</h1>",
        png=b"png-new",
        metrics={"title": {"text": "new", "visible": True}},
    )

    assert store.latest_revision(attempt_id="attempt-a", slide_index=2) == 1
    assert store.recover(attempt_id="attempt-a", slide_index=2) == repaired
    assert store.recover(attempt_id="attempt-a", slide_index=2, revision=0) == first
    payload = json.loads(
        (tmp_path / "attempt-a" / "slide-2" / "current.json").read_text("utf-8")
    )
    json.dumps(payload, allow_nan=False)


def test_stale_slide_repair_cannot_overwrite_newer_revision(tmp_path: Path):
    store = AtomicSlideCheckpointStore(tmp_path)
    store.commit(
        attempt_id="attempt-a", slide_index=0, revision=0,
        html="old", png=b"old", metrics={"v": 0},
    )
    store.commit_repair(
        attempt_id="attempt-a", slide_index=0, base_revision=0,
        html="new", png=b"new", metrics={"v": 1},
    )

    with pytest.raises(SlideCheckpointConflict, match="revision_(conflict|reused)"):
        store.commit_repair(
            attempt_id="attempt-a", slide_index=0, base_revision=0,
            html="stale", png=b"stale", metrics={"v": 2},
        )
    assert store.recover(attempt_id="attempt-a", slide_index=0).html == "new"


def test_corrupt_current_fails_closed_without_revision_fallback(tmp_path: Path):
    store = AtomicSlideCheckpointStore(tmp_path)
    store.commit(
        attempt_id="attempt-a", slide_index=1, revision=0,
        html="safe", png=b"png", metrics={"ok": True},
    )
    current = tmp_path / "attempt-a" / "slide-1" / "current.json"
    current.write_text(current.read_text("utf-8").replace("safe", "tampered"), "utf-8")

    with pytest.raises(SlideCheckpointCorrupt):
        store.recover(attempt_id="attempt-a", slide_index=1)
    # Historical revision remains available explicitly, but recover() never
    # silently falls back from a corrupt current pointer.
    assert store.recover(attempt_id="attempt-a", slide_index=1, revision=0).html == "safe"


def test_interrupted_repair_keeps_old_db_pointer_bytes(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path))
    job_id, token = uuid.uuid4(), "worker-token"
    base_path = render_artifact_path(job_id, 0, token)
    old_path = base_path.with_name(f"{base_path.stem}-r0-initial.png")
    atomic_write(old_path, b"old-png")
    write_valid_evidence(
        old_path,
        attempt_id=hashlib.sha256(token.encode()).hexdigest()[:32],
        slide_index=0,
        revision=0,
        html="old-html",
        png=b"old-png",
        metrics={"visible": True},
    )
    old_checkpoint = AuthoredSlideCheckpoint(
        job_id=job_id,
        slide_index=0,
        render_path=str(old_path.relative_to(tmp_path)).replace("\\", "/"),
        render_sha256=hashlib.sha256(b"old-png").hexdigest(),
        attempt_token=token,
    )
    # Simulate a staged repair that crashes before its DB update. It receives
    # a distinct immutable path, leaving the old pointer untouched.
    staged = old_path.with_name("slide-0-next-r1-interrupted.png")
    atomic_write(staged, b"new-png")
    assert read_valid_evidence(old_checkpoint).html == "old-html"


def test_recover_promotes_committed_revision_after_pointer_crash(tmp_path: Path):
    store = AtomicSlideCheckpointStore(tmp_path)
    committed = store.commit(
        attempt_id="attempt-a", slide_index=3, revision=0,
        html="recover-me", png=b"png", metrics={"ok": True},
    )
    slide_dir = tmp_path / "attempt-a" / "slide-3"
    (slide_dir / "current.json").unlink()
    marker = next(slide_dir.glob(".committed-r*.json"))
    recovered = store.recover(attempt_id="attempt-a", slide_index=3)
    assert recovered == committed
    assert (slide_dir / "current.json").exists()
    assert marker.exists()


def test_recover_promotes_highest_committed_revision_after_pointer_crash(tmp_path: Path):
    store = AtomicSlideCheckpointStore(tmp_path)
    store.commit(
        attempt_id="attempt-a", slide_index=4, revision=0,
        html="first", png=b"png-0", metrics={"revision": 0},
    )
    second = store.commit_repair(
        attempt_id="attempt-a", slide_index=4, base_revision=0,
        html="second", png=b"png-1", metrics={"revision": 1},
    )
    slide_dir = tmp_path / "attempt-a" / "slide-4"
    (slide_dir / "current.json").unlink()

    recovered = store.recover(attempt_id="attempt-a", slide_index=4)
    assert recovered == second
    assert recovered.revision == 1


def test_orphan_sweep_removes_stale_prepared_marker(tmp_path: Path):
    store = AtomicSlideCheckpointStore(tmp_path)
    slide_dir = tmp_path / "attempt-a" / "slide-0"
    slide_dir.mkdir(parents=True)
    prepared = slide_dir / ".prepared-r1-deadbeef.json"
    prepared.write_text('{"revision":1,"digest":"deadbeef"}', "utf-8")
    import os, time
    old = time.time() - 3600
    os.utime(prepared, (old, old))
    assert store.sweep_orphans(max_age_seconds=60) == 1
    assert not prepared.exists()
