"""Create a fresh authored-vNext A/B export run and bind PowerPoint renders.

This harness is intentionally split into two explicit phases. ``generate``
talks only to an already-running authored-vNext API and writes into a new,
timestamped directory. ``render`` refuses an existing output directory or an
already-running POWERPNT process before invoking the existing desktop QA
script, normalizes the exported PNG names, and writes atomic A/B receipts.

It never starts or stops the product stack and never sets promotion readiness.

Examples from the repository root::

    uv run --directory servers/fastapi python \
      ../../scripts/authored-vnext-quality/fresh_quality_run.py generate \
      --master-a-id master:company-a --master-a-revision 1 \
      --master-b-id master:company-b --master-b-revision 1 \
      --image-asset C:/absolute/path/reference.jpg

    uv run --directory servers/fastapi python \
      ../../scripts/authored-vnext-quality/fresh_quality_run.py render \
      --run-dir C:/absolute/path/authored-vnext-quality-20260821T120000Z-ab12 \
      --legacy-pptx C:/absolute/path/legacy-authored.pptx
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


MANIFEST_SCHEMA = "authored-vnext-visual-acceptance/v1"
ATOMIC_SCHEMA = "authored-vnext-quality-atomic-bundle/v1"
RENDER_SCHEMA = "authored-vnext-powerpoint-com-render/v1"
QUALITY_POLICY_VERSION = "authored-vnext-quality/v1"
DEFAULT_API_BASE = "http://127.0.0.1:8010/api/v1/ppt/authored-vnext"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RENDER_NAME_RE = re.compile(r"^(?:slide-|슬라이드)(\d+)$", re.IGNORECASE)


class HarnessFailure(RuntimeError):
    """Fail-closed operator or evidence error."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_manifest() -> Path:
    return Path(__file__).resolve().with_name("representative_deck_manifest.json")


def _default_powerpoint_script() -> Path:
    return _repo_root() / "servers" / "nextjs" / "scripts" / "qa-powerpoint-desktop.ps1"


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise HarnessFailure(f"{label}_missing:{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessFailure(f"{label}_invalid:{path}") from exc
    if not isinstance(value, dict):
        raise HarnessFailure(f"{label}_object_required:{path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _outline_from_manifest(path: Path) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessFailure(f"representative_manifest_invalid:{path}") from exc
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != MANIFEST_SCHEMA:
        raise HarnessFailure("representative_manifest_schema_unsupported")
    slides = manifest.get("slides")
    if not isinstance(slides, list) or len(slides) != 10:
        raise HarnessFailure("representative_manifest_exactly_ten_slides_required")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, slide in enumerate(slides):
        if not isinstance(slide, dict):
            raise HarnessFailure(f"representative_manifest_slide_invalid:{index + 1}")
        slide_id = slide.get("slideId")
        title = slide.get("title")
        summary = slide.get("summary")
        if not isinstance(slide_id, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]*", slide_id
        ):
            raise HarnessFailure(f"representative_manifest_slide_id_invalid:{index + 1}")
        if slide_id in seen:
            raise HarnessFailure(f"representative_manifest_slide_id_duplicate:{slide_id}")
        seen.add(slide_id)
        if not isinstance(title, str) or not title.strip():
            raise HarnessFailure(f"representative_manifest_title_invalid:{index + 1}")
        if not isinstance(summary, str) or not summary.strip():
            raise HarnessFailure(f"representative_manifest_summary_invalid:{index + 1}")
        role = slide.get("role")
        role_hint = "cover" if role == "cover" else "longTitle" if role == "long-title" else "standard"
        items.append(
            {
                "itemId": f"outline:{slide_id}",
                "index": index,
                "title": title,
                "summary": summary,
                "roleHint": role_hint,
            }
        )
    source_digest = _sha256_bytes(raw)
    outline = {
        "schemaVersion": "authored-outline/v1",
        "sourceDigest": source_digest,
        "deckTitle": "Authored와 authored-vNext 시각 품질 비교",
        "items": items,
    }
    return manifest, outline, raw


class ApiClient:
    def __init__(self, base_url: str, bearer_token: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Accept": "application/json"}
        if bearer_token:
            self.headers["Authorization"] = f"Bearer {bearer_token}"

    def json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 180,
    ) -> dict[str, Any]:
        body = None
        headers = dict(self.headers)
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                value = json.loads(response.read())
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:4000]
            raise HarnessFailure(f"api_http_error:{method}:{path}:{exc.code}:{detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise HarnessFailure(f"api_request_failed:{method}:{path}:{exc}") from exc
        if not isinstance(value, dict):
            raise HarnessFailure(f"api_response_object_required:{method}:{path}")
        return value

    def bytes(self, path: str, *, timeout: float = 180) -> bytes:
        request = Request(self.base_url + path, method="GET", headers=self.headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:4000]
            raise HarnessFailure(f"api_http_error:GET:{path}:{exc.code}:{detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise HarnessFailure(f"api_request_failed:GET:{path}:{exc}") from exc


def _master_pin(client: ApiClient, master_id: str, revision: int) -> dict[str, Any]:
    value = client.json(
        "GET",
        f"/generation/master-pins/{quote(master_id, safe='')}/{revision}",
        timeout=60,
    )
    required = (
        "masterId",
        "masterRevision",
        "masterDigest",
        "layoutRegistryDigest",
        "fontManifestDigest",
    )
    if any(value.get(key) in (None, "") for key in required):
        raise HarnessFailure(f"published_master_pin_incomplete:{master_id}:{revision}")
    return value


def _asset_payload(path: Path, alt_text: str) -> dict[str, Any]:
    if not path.is_file():
        raise HarnessFailure(f"image_asset_missing:{path}")
    value = path.read_bytes()
    media_type = mimetypes.guess_type(path.name)[0]
    if media_type not in {"image/jpeg", "image/png"}:
        raise HarnessFailure("image_asset_must_be_png_or_jpeg")
    digest = _sha256_bytes(value)
    return {
        "assetId": "asset:" + digest.removeprefix("sha256:")[:48],
        "assetDigest": digest,
        "altText": alt_text,
        "crop": "cover",
        "mediaType": media_type,
        "storageReference": (
            f"data:{media_type};base64," + base64.b64encode(value).decode("ascii")
        ),
        "sizeBytes": len(value),
    }


def _structured_generation_payload(
    manifest: dict[str, Any], asset: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Bind the representative semantic roles to native, digest-pinned sources."""
    scene_inputs: list[dict[str, Any]] = []
    data_manifest: list[dict[str, Any]] = []
    table_payload = {
        "cells": [
            ["검증 항목", "Authored", "authored-vNext"],
            ["시각 품질", "82", "96"],
            ["네이티브 편집", "제한", "지원"],
            ["합계", "82", "96"],
        ]
    }
    chart_payload = {
        "categories": ["구도", "타이포", "편집성", "안정성"],
        "series": [
            {"name": "Authored", "values": [91, 88, 58, 90]},
            {"name": "authored-vNext", "values": [96, 94, 98, 97]},
        ],
    }
    payloads = {"table": table_payload, "chart": chart_payload}
    for slide in manifest["slides"]:
        role = slide.get("role")
        if role in {"cover", "long-title"}:
            continue
        item: dict[str, Any] = {
            "outlineItemId": f"outline:{slide['slideId']}",
            "roleHint": role,
        }
        if role == "image":
            item["sourceImage"] = {
                "assetId": asset["assetId"],
                "assetDigest": asset["assetDigest"],
                "altText": asset["altText"],
                "crop": asset["crop"],
            }
        elif role in payloads:
            payload = payloads[role]
            digest = _canonical_digest(payload)
            data_id = "data:" + digest.removeprefix("sha256:")[:48]
            source = {
                "dataId": data_id,
                "dataDigest": digest,
                "kind": role,
            }
            if role == "table":
                source.update(rows=len(payload["cells"]), columns=len(payload["cells"][0]))
            else:
                source.update(
                    chartType="column",
                    accessibilitySummary=(
                        "authored-vNext가 구도, 타이포, 편집성, 안정성 전 항목에서 높은 품질 점수"
                    ),
                )
            item["sourceData"] = source
            data_manifest.append(
                {
                    "dataId": data_id,
                    "dataDigest": digest,
                    "kind": role,
                    "payload": payload,
                }
            )
        scene_inputs.append(item)
    asset_manifest = [
        {
            "assetId": asset["assetId"],
            "assetDigest": asset["assetDigest"],
            "mediaType": asset["mediaType"],
            "storageReference": asset["storageReference"],
            "sizeBytes": asset["sizeBytes"],
        }
    ]
    return scene_inputs, asset_manifest, data_manifest


def _scene_body_bytes(deck: dict[str, Any]) -> bytes:
    scene = deck.get("scene")
    if not isinstance(scene, dict) or scene.get("schemaVersion") != "deck-scene-ir/v1":
        raise HarnessFailure("generated_deck_scene_missing")
    slides = scene.get("slides")
    if not isinstance(slides, list) or len(slides) != 10:
        raise HarnessFailure("generated_scene_exactly_ten_slides_required")
    body = {
        "schemaVersion": scene["schemaVersion"],
        "slides": [
            {"slideId": slide.get("slideId"), "elements": slide.get("elements")}
            for slide in slides
            if isinstance(slide, dict)
        ],
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _export_deck(
    client: ApiClient,
    *,
    label: str,
    deck: dict[str, Any],
    token: str,
) -> tuple[dict[str, Any], bytes]:
    scene = deck.get("scene")
    master = deck.get("master")
    if not isinstance(scene, dict) or not isinstance(master, dict):
        raise HarnessFailure(f"export_snapshot_incomplete:{label}")
    asset_digests = [
        item.get("assetDigest")
        for item in scene.get("assetManifest", [])
        if isinstance(item, dict) and isinstance(item.get("assetDigest"), str)
    ]
    created = client.json(
        "POST",
        "/export/jobs",
        {
            "deckId": deck["deckId"],
            "expectedDeckRevision": deck["revision"],
            "expectedDeckDigest": deck["digest"],
            "expectedMasterRevision": master["masterRevision"],
            "expectedMasterDigest": master["masterDigest"],
            "expectedActiveArtifactRevision": 0,
            "expectedActiveArtifactDigest": None,
            "expectedSourcePackageDigest": None,
            "expectedAssetDigests": asset_digests,
            "expectedFontDigest": master["fontManifestDigest"],
            "idempotencyKey": f"quality-{label}-export-{token}",
            "operationFence": 1,
            "timeoutMs": 180000,
            "maxAttempts": 3,
        },
    )
    job_id = created.get("jobId")
    if not isinstance(job_id, str):
        raise HarnessFailure(f"export_job_id_missing:{label}")
    completed = client.json("POST", f"/export/jobs/{quote(job_id, safe='')}/run", {})
    artifact = completed.get("artifact")
    if (
        completed.get("state") != "succeeded"
        or completed.get("attemptCount") != 1
        or not isinstance(artifact, dict)
        or artifact.get("readBackVerified") is not True
    ):
        raise HarnessFailure(f"export_not_first_attempt_readback_verified:{label}")
    pptx = client.bytes(
        f"/export/decks/{quote(str(deck['deckId']), safe='')}/artifact"
    )
    if _sha256_bytes(pptx) != artifact.get("digest"):
        raise HarnessFailure(f"downloaded_artifact_digest_mismatch:{label}")
    return completed, pptx


def _resolve_bearer(args: argparse.Namespace) -> str | None:
    name = args.bearer_token_env
    if not name:
        return None
    value = os.getenv(name)
    if not value:
        raise HarnessFailure(f"bearer_token_environment_variable_missing:{name}")
    return value


def _new_output_directory(value: str | None) -> tuple[Path, str]:
    token = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + secrets.token_hex(4)
    output = (
        Path(value).expanduser().resolve()
        if value
        else (_repo_root() / "artifacts" / f"authored-vnext-quality-{token}").resolve()
    )
    if output.exists():
        raise HarnessFailure(f"output_directory_must_not_exist:{output}")
    output.mkdir(parents=True, exist_ok=False)
    return output, token


def generate(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest, outline, manifest_bytes = _outline_from_manifest(manifest_path)
    output, token = _new_output_directory(args.output_dir)
    summary: dict[str, Any] = {
        "schemaVersion": "authored-vnext-quality-fresh-run/v1",
        "status": "running",
        "promotionReady": False,
        "runToken": token,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "apiBase": args.api_base.rstrip("/"),
        "manifestSha256": _sha256_bytes(manifest_bytes),
        "outputs": {},
    }
    _write_json(output / "run-summary.json", summary)
    (output / "representative-deck-manifest.json").write_bytes(manifest_bytes)
    try:
        client = ApiClient(args.api_base, _resolve_bearer(args))
        pins = {
            "a": _master_pin(client, args.master_a_id, args.master_a_revision),
            "b": _master_pin(client, args.master_b_id, args.master_b_revision),
        }
        if pins["a"]["masterDigest"] == pins["b"]["masterDigest"]:
            raise HarnessFailure("ab_master_digests_must_differ")
        asset = _asset_payload(
            Path(args.image_asset).expanduser().resolve(), args.image_alt_text
        )
        scene_inputs, asset_manifest, data_manifest = _structured_generation_payload(
            manifest, asset
        )
        decks: dict[str, dict[str, Any]] = {}
        exports: dict[str, dict[str, Any]] = {}
        for label in ("a", "b"):
            deck_id = f"deck:quality-{label}-{token}"
            operation_id = f"operation:quality-{label}-generate-{token}"
            result = client.json(
                "POST",
                "/generation/decks",
                {
                    "idempotencyKey": f"quality-{label}-generate-{token}",
                    "command": {
                        "schemaVersion": "generate-deck-request/v1",
                        "operationId": operation_id,
                        "deckId": deck_id,
                        "master": pins[label],
                        "outline": outline,
                        "slideSceneInputs": scene_inputs,
                        "assetManifest": asset_manifest,
                        "dataManifest": data_manifest,
                        "qualityPolicyVersion": QUALITY_POLICY_VERSION,
                        "publicationFence": {
                            "expectedDeckRevision": 0,
                            "expectedActiveRevisionDigest": None,
                        },
                    },
                },
            )
            deck = result.get("deck")
            quality = result.get("quality")
            if (
                not isinstance(deck, dict)
                or not isinstance(quality, dict)
                or quality.get("passed") is not True
            ):
                raise HarnessFailure(f"generated_deck_quality_failed:{label}")
            export, pptx = _export_deck(
                client, label=label, deck=deck, token=token
            )
            current = client.json(
                "GET",
                f"/generation/decks/{quote(str(deck_id), safe='')}",
                timeout=60,
            ).get("deck")
            if not isinstance(current, dict) or current.get("digest") != deck.get("digest"):
                raise HarnessFailure(f"active_deck_snapshot_mismatch:{label}")
            decks[label] = current
            exports[label] = export
            _write_json(output / f"deck-{label}.json", current)
            _write_json(output / f"scene-{label}.json", current["scene"])
            _write_json(output / f"export-{label}.json", export)
            (output / f"{label}.pptx").write_bytes(pptx)
        body_a = _scene_body_bytes(decks["a"])
        body_b = _scene_body_bytes(decks["b"])
        if body_a != body_b:
            raise HarnessFailure("ab_raw_body_scene_byte_identity_mismatch")
        summary.update(
            status="generated",
            completedAt=datetime.now(timezone.utc).isoformat(),
            slideCount=len(manifest["slides"]),
            rawBodySceneSha256=_sha256_bytes(body_a),
            outputs={
                label: {
                    "deckId": decks[label]["deckId"],
                    "revision": decks[label]["revision"],
                    "deckDigest": decks[label]["digest"],
                    "sceneDigest": decks[label]["scene"]["sceneDigest"],
                    "master": pins[label],
                    "exportJobId": exports[label]["jobId"],
                    "exportAttemptCount": exports[label]["attemptCount"],
                    "pptx": str(output / f"{label}.pptx"),
                    "pptxSha256": _sha256_file(output / f"{label}.pptx"),
                }
                for label in ("a", "b")
            },
        )
        _write_json(output / "run-summary.json", summary)
        return {"status": "generated", "runDirectory": str(output)}
    except Exception as exc:
        summary.update(
            status="failed",
            completedAt=datetime.now(timezone.utc).isoformat(),
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        _write_json(output / "run-summary.json", summary)
        raise


def generate_workbench(args: argparse.Namespace) -> dict[str, Any]:
    """Create one fresh, unpublished-artifact deck for real Workbench save/export QA."""
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest, outline, manifest_bytes = _outline_from_manifest(manifest_path)
    output, token = _new_output_directory(args.output_dir)
    client = ApiClient(args.api_base, _resolve_bearer(args))
    pin = _master_pin(client, args.master_id, args.master_revision)
    asset = _asset_payload(
        Path(args.image_asset).expanduser().resolve(), args.image_alt_text
    )
    scene_inputs, asset_manifest, data_manifest = _structured_generation_payload(
        manifest, asset
    )
    deck_id = f"deck:quality-workbench-{token}"
    result = client.json(
        "POST",
        "/generation/decks",
        {
            "idempotencyKey": f"quality-workbench-generate-{token}",
            "command": {
                "schemaVersion": "generate-deck-request/v1",
                "operationId": f"operation:quality-workbench-generate-{token}",
                "deckId": deck_id,
                "master": pin,
                "outline": outline,
                "slideSceneInputs": scene_inputs,
                "assetManifest": asset_manifest,
                "dataManifest": data_manifest,
                "qualityPolicyVersion": QUALITY_POLICY_VERSION,
                "publicationFence": {
                    "expectedDeckRevision": 0,
                    "expectedActiveRevisionDigest": None,
                },
            },
        },
    )
    deck = result.get("deck")
    quality = result.get("quality")
    if (
        not isinstance(deck, dict)
        or not isinstance(quality, dict)
        or quality.get("passed") is not True
        or deck.get("revision") != 1
    ):
        raise HarnessFailure("workbench_generated_deck_quality_failed")
    _write_json(output / "deck-workbench.json", deck)
    _write_json(output / "scene-workbench.json", deck["scene"])
    summary = {
        "schemaVersion": "authored-vnext-quality-workbench-run/v1",
        "status": "generated",
        "promotionReady": False,
        "runToken": token,
        "manifestSha256": _sha256_bytes(manifest_bytes),
        "deckId": deck_id,
        "revision": deck["revision"],
        "deckDigest": deck["digest"],
        "sceneDigest": deck["scene"]["sceneDigest"],
        "master": pin,
        "artifactCreated": False,
    }
    _write_json(output / "workbench-summary.json", summary)
    return {"status": "generated", "runDirectory": str(output), "deckId": deck_id}


def _powershell_executable() -> str:
    value = shutil.which("pwsh") or shutil.which("powershell")
    if value is None:
        raise HarnessFailure("powershell_executable_missing")
    return value


def _powerpoint_process_ids(shell: str) -> list[int]:
    command = (
        "$p=@(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue);"
        "@($p | ForEach-Object {[int]$_.Id}) | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    text = result.stdout.strip()
    if not text:
        return []
    value = json.loads(text)
    return [int(item) for item in value] if isinstance(value, list) else [int(value)]


def _ps_quote(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _slide_index(path: Path) -> int:
    match = _RENDER_NAME_RE.fullmatch(path.stem)
    if match is None:
        raise HarnessFailure(f"powerpoint_render_name_invalid:{path.name}")
    return int(match.group(1))


def _normalize_render_directory(directory: Path, slide_count: int) -> list[Path]:
    files = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".png"),
        key=_slide_index,
    )
    indices = [_slide_index(path) for path in files]
    if indices != list(range(1, slide_count + 1)):
        raise HarnessFailure(
            f"powerpoint_render_indices_invalid:{directory}:{indices}"
        )
    temporary: list[tuple[Path, Path]] = []
    for index, source in enumerate(files, start=1):
        temp = directory / f".__normalize-{index}.png"
        source.replace(temp)
        temporary.append((temp, directory / f"slide-{index}.png"))
    for source, target in temporary:
        if target.exists():
            raise HarnessFailure(f"normalized_render_target_exists:{target}")
        source.replace(target)
    return [target for _, target in temporary]


def _render_receipt(
    *,
    run_token: str,
    source: Path,
    directory: Path,
    files: list[Path],
    desktop_report: dict[str, Any],
) -> dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise HarnessFailure("pillow_required_for_render_evidence") from exc
    slides = []
    for index, path in enumerate(files, start=1):
        with Image.open(path) as image:
            image.load()
            width, height = image.size
        if width < 640 or height < 360:
            raise HarnessFailure(f"powerpoint_render_too_small:{path}")
        slides.append(
            {
                "slide": index,
                "path": str(path),
                "sha256": _sha256_file(path),
                "width": width,
                "height": height,
            }
        )
    receipt = {
        "schemaVersion": RENDER_SCHEMA,
        "runToken": run_token,
        "renderer": f"powerpoint-com/{desktop_report.get('PowerPointVersion')}",
        "capturedAt": datetime.now(timezone.utc).isoformat(),
        "sourcePptx": {
            "path": str(source),
            "sha256": _sha256_file(source),
            "bytes": source.stat().st_size,
        },
        "directory": str(directory),
        "slideCount": len(files),
        "pngSha256": [item["sha256"] for item in slides],
        "slides": slides,
    }
    receipt["evidenceDigest"] = _canonical_digest(receipt)
    return receipt


def _contact_sheet(left: list[Path], right: list[Path], output: Path) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise HarnessFailure("pillow_required_for_contact_sheet") from exc
    if len(left) != len(right) or not left:
        raise HarnessFailure("contact_sheet_complete_pair_required")
    cell = (384, 216)
    label_height = 24
    sheet = Image.new(
        "RGB", (cell[0] * 2, (cell[1] + label_height) * len(left)), "white"
    )
    draw = ImageDraw.Draw(sheet)
    for row, (left_path, right_path) in enumerate(zip(left, right)):
        y = row * (cell[1] + label_height)
        with Image.open(left_path) as source:
            sheet.paste(source.convert("RGB").resize(cell), (0, y + label_height))
        with Image.open(right_path) as source:
            sheet.paste(
                source.convert("RGB").resize(cell), (cell[0], y + label_height)
            )
        draw.text((8, y + 5), f"slide {row + 1} / left", fill="#111827")
        draw.text((cell[0] + 8, y + 5), f"slide {row + 1} / right", fill="#111827")
    sheet.save(output)


def _atomic_manifest(
    *,
    deck: dict[str, Any],
    export: dict[str, Any],
    pptx: Path,
    render: dict[str, Any],
) -> dict[str, Any]:
    artifact = export.get("artifact")
    if not isinstance(artifact, dict):
        raise HarnessFailure("atomic_export_artifact_missing")
    pptx_sha = _sha256_file(pptx)
    if artifact.get("digest") != pptx_sha:
        raise HarnessFailure("atomic_export_pptx_digest_mismatch")
    source = render.get("sourcePptx")
    if not isinstance(source, dict) or source.get("sha256") != pptx_sha:
        raise HarnessFailure("atomic_render_source_pptx_mismatch")
    manifest = {
        "schemaVersion": ATOMIC_SCHEMA,
        "deckId": deck.get("deckId"),
        "revision": deck.get("revision"),
        "deckDigest": deck.get("digest"),
        "sceneDigest": (deck.get("scene") or {}).get("sceneDigest"),
        "publicationRevision": artifact.get("publicationRevision"),
        "artifactDigest": artifact.get("digest"),
        "pptxSha256": pptx_sha,
        "pngSha256": render.get("pngSha256"),
        "jobId": export.get("jobId"),
        "attemptCount": export.get("attemptCount"),
        "readBackVerified": artifact.get("readBackVerified"),
        "renderEvidenceDigest": render.get("evidenceDigest"),
    }
    required = (
        "deckId",
        "revision",
        "deckDigest",
        "sceneDigest",
        "publicationRevision",
        "artifactDigest",
        "pptxSha256",
        "pngSha256",
    )
    if any(manifest.get(key) in (None, "") for key in required):
        raise HarnessFailure("atomic_manifest_required_field_missing")
    if export.get("attemptCount") != 1 or artifact.get("readBackVerified") is not True:
        raise HarnessFailure("atomic_export_not_first_attempt_readback_verified")
    manifest["evidenceDigest"] = _canonical_digest(manifest)
    return manifest


def render(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).expanduser().resolve()
    summary = _read_json(run_dir / "run-summary.json", "fresh_run_summary")
    if summary.get("status") != "generated" or summary.get("promotionReady") is not False:
        raise HarnessFailure("fresh_generated_run_summary_required")
    run_token = summary.get("runToken")
    if not isinstance(run_token, str) or not run_token:
        raise HarnessFailure("fresh_run_token_missing")
    inputs = {"a": run_dir / "a.pptx", "b": run_dir / "b.pptx"}
    if args.legacy_pptx:
        inputs["legacy"] = Path(args.legacy_pptx).expanduser().resolve()
    for label, path in inputs.items():
        if not path.is_file():
            raise HarnessFailure(f"render_input_missing:{label}:{path}")
    if len({path.stem.casefold() for path in inputs.values()}) != len(inputs):
        raise HarnessFailure("render_input_basenames_must_be_unique")
    powerpoint_script = Path(args.powerpoint_script).expanduser().resolve()
    if not powerpoint_script.is_file():
        raise HarnessFailure(f"powerpoint_qa_script_missing:{powerpoint_script}")
    output_root = run_dir / "powerpoint-com"
    if output_root.exists():
        raise HarnessFailure(f"powerpoint_output_must_not_exist:{output_root}")
    shell = _powershell_executable()
    before = _powerpoint_process_ids(shell)
    if before:
        raise HarnessFailure(
            "preexisting_powerpoint_processes:" + ",".join(str(item) for item in before)
        )
    presentations = ",".join(_ps_quote(path) for path in inputs.values())
    command = (
        f"& {_ps_quote(powerpoint_script)} -Presentation @({presentations}) "
        f"-OutputDirectory {_ps_quote(output_root)}"
    )
    completed = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise HarnessFailure(
            f"powerpoint_qa_failed:{completed.returncode}:{completed.stderr[-4000:]}"
        )
    process_deadline = time.monotonic() + 15
    after = _powerpoint_process_ids(shell)
    while after and time.monotonic() < process_deadline:
        time.sleep(0.25)
        after = _powerpoint_process_ids(shell)
    if after:
        raise HarnessFailure(
            "powerpoint_processes_remain_after_render:"
            + ",".join(str(item) for item in after)
        )
    desktop_report = _read_json(
        output_root / "powerpoint-desktop-validation.json",
        "powerpoint_desktop_validation",
    )
    result_by_path = {
        str(Path(item.get("Path", "")).resolve()).casefold(): item
        for item in desktop_report.get("Presentations", [])
        if isinstance(item, dict)
    }
    receipts: dict[str, dict[str, Any]] = {}
    normalized: dict[str, list[Path]] = {}
    for label, source in inputs.items():
        result = result_by_path.get(str(source).casefold())
        if not isinstance(result, dict):
            raise HarnessFailure(f"powerpoint_result_missing:{label}")
        slide_count = result.get("SlideCount")
        if (
            not isinstance(slide_count, int)
            or result.get("OpenedReadOnly") is not True
            or result.get("ExportedPngs") != slide_count
        ):
            raise HarnessFailure(f"powerpoint_result_invalid:{label}")
        directory = output_root / source.stem
        normalized[label] = _normalize_render_directory(directory, slide_count)
        receipts[label] = _render_receipt(
            run_token=run_token,
            source=source,
            directory=directory,
            files=normalized[label],
            desktop_report=desktop_report,
        )
        _write_json(run_dir / f"render-{label}-evidence.json", receipts[label])
    if len(normalized["a"]) != 10 or len(normalized["b"]) != 10:
        raise HarnessFailure("ab_powerpoint_render_exactly_ten_slides_required")
    contact_ab = run_dir / "contact-sheet-ab.png"
    _contact_sheet(normalized["a"], normalized["b"], contact_ab)
    atomic: dict[str, dict[str, Any]] = {}
    for label in ("a", "b"):
        atomic[label] = _atomic_manifest(
            deck=_read_json(run_dir / f"deck-{label}.json", f"deck_{label}"),
            export=_read_json(run_dir / f"export-{label}.json", f"export_{label}"),
            pptx=inputs[label],
            render=receipts[label],
        )
        _write_json(run_dir / f"atomic-{label}.json", atomic[label])
    contact_legacy = None
    if "legacy" in normalized:
        if len(normalized["legacy"]) != 10:
            raise HarnessFailure("legacy_powerpoint_render_exactly_ten_slides_required")
        contact_legacy = run_dir / "contact-sheet-legacy-vnext-a.png"
        _contact_sheet(normalized["legacy"], normalized["a"], contact_legacy)
    result = {
        "schemaVersion": "authored-vnext-quality-render-run/v1",
        "status": "rendered",
        "promotionReady": False,
        "runToken": run_token,
        "completedAt": datetime.now(timezone.utc).isoformat(),
        "powerPointVersion": desktop_report.get("PowerPointVersion"),
        "contactSheetAb": {
            "path": str(contact_ab),
            "sha256": _sha256_file(contact_ab),
        },
        "contactSheetLegacyVNext": (
            {"path": str(contact_legacy), "sha256": _sha256_file(contact_legacy)}
            if contact_legacy is not None
            else None
        ),
        "renderEvidence": {
            label: str(run_dir / f"render-{label}-evidence.json")
            for label in receipts
        },
        "atomicEvidence": {
            label: str(run_dir / f"atomic-{label}.json") for label in atomic
        },
    }
    _write_json(run_dir / "render-summary.json", result)
    return result


def validate(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest, outline, raw = _outline_from_manifest(manifest_path)
    powerpoint_script = Path(args.powerpoint_script).expanduser().resolve()
    if not powerpoint_script.is_file():
        raise HarnessFailure(f"powerpoint_qa_script_missing:{powerpoint_script}")
    return {
        "status": "valid",
        "manifest": str(manifest_path),
        "manifestSha256": _sha256_bytes(raw),
        "slideCount": len(manifest["slides"]),
        "outlineItemIds": [item["itemId"] for item in outline["items"]],
        "powerpointScript": str(powerpoint_script),
        "writesPerformed": False,
        "promotionReady": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    generation = subparsers.add_parser(
        "generate", help="Generate and export fresh ten-slide A/B decks through the live API."
    )
    generation.add_argument("--manifest", default=str(_default_manifest()))
    generation.add_argument("--api-base", default=DEFAULT_API_BASE)
    generation.add_argument("--master-a-id", required=True)
    generation.add_argument("--master-a-revision", required=True, type=int)
    generation.add_argument("--master-b-id", required=True)
    generation.add_argument("--master-b-revision", required=True, type=int)
    generation.add_argument("--image-asset", required=True)
    generation.add_argument(
        "--image-alt-text", default="authored-vNext 품질 검증 이미지"
    )
    generation.add_argument("--output-dir")
    generation.add_argument(
        "--bearer-token-env",
        help="Read an optional bearer token from this environment variable; never pass a token on the command line.",
    )
    generation.set_defaults(handler=generate)

    workbench = subparsers.add_parser(
        "workbench",
        help="Generate one fresh ten-slide deck with no artifact for Workbench save/export QA.",
    )
    workbench.add_argument("--manifest", default=str(_default_manifest()))
    workbench.add_argument("--api-base", default=DEFAULT_API_BASE)
    workbench.add_argument("--master-id", required=True)
    workbench.add_argument("--master-revision", required=True, type=int)
    workbench.add_argument("--image-asset", required=True)
    workbench.add_argument(
        "--image-alt-text", default="authored-vNext Workbench 품질 검증 이미지"
    )
    workbench.add_argument("--output-dir")
    workbench.add_argument("--bearer-token-env")
    workbench.set_defaults(handler=generate_workbench)

    rendering = subparsers.add_parser(
        "render", help="Render a generated A/B run with isolated PowerPoint COM evidence."
    )
    rendering.add_argument("--run-dir", required=True)
    rendering.add_argument("--legacy-pptx")
    rendering.add_argument(
        "--powerpoint-script", default=str(_default_powerpoint_script())
    )
    rendering.set_defaults(handler=render)

    validation = subparsers.add_parser(
        "validate", help="Validate the checked-in manifest and wrapper dependencies without writes."
    )
    validation.add_argument("--manifest", default=str(_default_manifest()))
    validation.add_argument(
        "--powerpoint-script", default=str(_default_powerpoint_script())
    )
    validation.set_defaults(handler=validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = args.handler(args)
    except HarnessFailure as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
