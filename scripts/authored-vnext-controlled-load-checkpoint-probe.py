"""Isolated checkpoint durability probe for the controlled-load harness.

This is not public API/worker evidence. It executes the production checkpoint
classes against a caller-owned directory and records exactly which fail-closed
and orphan-sweep contracts were observed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import uuid


REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "servers" / "fastapi"
EVIDENCE_ROOT = REPO / "artifacts" / "authored-vnext-controlled-load-20260829"


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--crash-writer", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--store-root", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args()


def _modules(root: Path):
    os.environ["APP_DATA_DIRECTORY"] = str(root / "app-data")
    sys.path.insert(0, str(SERVER))
    from services import authored_checkpoint_artifacts as artifacts
    from services.authored_slide_checkpoint_bundle import (
        AtomicSlideCheckpointStore,
        SlideCheckpointBundle,
        SlideCheckpointCorrupt,
    )

    return artifacts, AtomicSlideCheckpointStore, SlideCheckpointBundle, SlideCheckpointCorrupt


def _crash_writer(args: argparse.Namespace) -> None:
    if args.store_root is None:
        raise SystemExit("--store-root required")
    _, Store, Bundle, _ = _modules(args.root.resolve())
    store = Store(args.store_root)
    attempt_id = "crash-attempt"
    slide_index = 0
    revision = 1
    html = "<section>crash-safe</section>"
    png = b"crash-safe-png"
    metrics = {"visible": True, "revision": revision}
    digest = Bundle._digest_for_payload(html, png, metrics)
    bundle = Bundle(attempt_id, slide_index, revision, html, png, metrics, digest)
    slide_dir = store._slide_dir(attempt_id, slide_index, create=True)
    Store._atomic_file(slide_dir / f"revision-{revision}.json", Store._json(bundle.to_payload()))
    Store._atomic_file(
        slide_dir / f".committed-r{revision}-{digest[:16]}.json",
        Store._json({"revision": revision, "digest": digest}),
    )
    os._exit(91)


def _expect_corrupt(action, error_type) -> str:
    try:
        action()
    except error_type as exc:
        return str(exc)
    raise AssertionError("checkpoint mismatch did not fail closed")


def _run(args: argparse.Namespace) -> dict[str, object]:
    root = args.root.resolve()
    output = args.output.resolve()
    if not _inside(root, EVIDENCE_ROOT) or not _inside(output, EVIDENCE_ROOT):
        raise ValueError("root and output must be below the controlled evidence directory")
    root.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts, Store, Bundle, Corrupt = _modules(root)

    job_id = uuid.uuid4()
    attempt_token = "controlled-load-attempt"
    attempt_id = artifacts.evidence_attempt_id(attempt_token)
    base_path = artifacts.render_artifact_path(job_id, 0, attempt_token)
    render_path = base_path.with_name(f"{base_path.stem}-r0-probe.png")
    html = "<section>durable evidence</section>"
    png = b"durable-png"
    metrics = {"revision": 0, "visible": True}
    artifacts.atomic_write(render_path, png)
    artifacts.write_valid_evidence(
        render_path,
        attempt_id=attempt_id,
        slide_index=0,
        revision=0,
        html=html,
        png=png,
        metrics=metrics,
    )
    relative_render = str(render_path.relative_to(artifacts.checkpoint_root())).replace(os.sep, "/")
    checkpoint = SimpleNamespace(
        render_path=relative_render,
        render_sha256=hashlib.sha256(png).hexdigest(),
        illustrated_html=html,
        slide_index=0,
    )
    valid = artifacts.read_valid_evidence(checkpoint)
    if valid is None or valid.digest != Bundle._digest_for_payload(html, png, metrics):
        raise AssertionError("valid evidence did not read back")

    checkpoint.render_sha256 = "0" * 64
    db_to_filesystem = _expect_corrupt(lambda: artifacts.read_valid_evidence(checkpoint), Corrupt)
    checkpoint.render_sha256 = hashlib.sha256(png).hexdigest()

    sidecar = render_path.with_name(f".{render_path.name}.evidence.json")
    current_payload = json.loads(sidecar.read_text("utf-8"))
    current_payload["metrics"]["visible"] = False
    current_payload["digest"] = Bundle._digest_for_payload(
        current_payload["html"], png, current_payload["metrics"]
    )
    sidecar.write_text(json.dumps(current_payload, ensure_ascii=False), encoding="utf-8")
    filesystem_to_immutable = _expect_corrupt(
        lambda: artifacts.read_valid_evidence(checkpoint), Corrupt
    )

    store_root = root / "bundle-store"
    crashed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--root",
            str(root),
            "--output",
            str(output),
            "--crash-writer",
            "--store-root",
            str(store_root),
        ],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if crashed.returncode != 91:
        raise AssertionError(f"crash writer returned {crashed.returncode}: {crashed.stderr}")
    recovered = Store(store_root).recover(attempt_id="crash-attempt", slide_index=0)
    if recovered.revision != 1 or not (store_root / "crash-attempt" / "slide-0" / "current.json").is_file():
        raise AssertionError("committed immutable bundle was not reconciled to current")

    orphan_dir = store_root / "orphan-attempt" / "slide-0"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    orphan = orphan_dir / ".prepared-r1-deadbeef.json"
    orphan.write_text('{"revision":1,"digest":"deadbeef"}', encoding="utf-8")
    removed = Store(store_root).sweep_orphans(max_age_seconds=0, max_files=1)
    if removed != 1 or orphan.exists():
        raise AssertionError("bounded orphan sweep did not delete exactly the owned orphan")

    report = {
        "schemaVersion": 1,
        "kind": "authored-vnext-checkpoint-probe",
        "productPublicApiExecuted": False,
        "ownedWorkerCrashExecuted": False,
        "isolatedProcessCrashExecuted": True,
        "immutableBundleRecovery": {
            "passed": True,
            "childExitCode": crashed.returncode,
            "revision": recovered.revision,
            "digest": recovered.digest,
        },
        "digestMismatchFailClosed": {
            "dbToFilesystem": db_to_filesystem,
            "filesystemCurrentToImmutable": filesystem_to_immutable,
        },
        "orphanSweep": {"passed": True, "deletedFiles": removed, "ownedPath": str(orphan)},
        "notVerified": [
            "public FastAPI to external worker crash recovery",
            "real presentation job DB pointer recovery",
            "PPTX/PDF generation I/O",
            "seven-day wall-clock soak",
        ],
    }
    temporary = output.with_suffix(f"{output.suffix}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return report


def main() -> None:
    args = _args()
    if args.crash_writer:
        _crash_writer(args)
    report = _run(args)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

