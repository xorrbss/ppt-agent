#!/usr/bin/env python3
"""Seed one real Authored 2-slide semantic vertical slice into product SQLite.

This is an additive local fixture command.  It uses the product SQL models and
the atomic semantic publication service; it never calls the LLM, mocks a
browser/exporter, or rewrites an existing presentation.  The resulting row is
therefore suitable for the official UI and export routes to consume.

Run from the repository root, for example:

    uv run python scripts/seed-authored-semantic-vertical-slice.py \
      --database-url sqlite:///app_data/fastapi.db

The command prints a JSON seed receipt containing the presentation id,
revision, operation lineage, active manifest digest, and the exact QID set.
The ``--font-path`` and CJK face paths must point at actual font bytes.  On
Windows, Malgun Gothic and Batang are used as defaults when present.  The
command fails closed when any required face is unavailable; it never labels
placeholder bytes as a real font.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
FASTAPI_ROOT = REPO_ROOT / "servers" / "fastapi"
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))


# Imports intentionally happen after DATABASE_URL is configured in main().
_IMAGE_PATH = REPO_ROOT / "templates" / "general" / "static" / "image3-82be19a88965.png"
if not _IMAGE_PATH.is_file():
    raise RuntimeError(f"bundled_image_missing:{_IMAGE_PATH}")
_PNG_BYTES = _IMAGE_PATH.read_bytes()
_IMAGE_DATA_URI = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode("ascii")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _locator_digest(value: str) -> str:
    return _sha256(value.encode("utf-8"))


def _artifact_ref(digest: str) -> str:
    return "artifact://sha256/" + digest.removeprefix("sha256:")


def _app_data_directory(database_url: str | None) -> Path:
    configured = os.getenv("APP_DATA_DIRECTORY")
    if configured:
        return Path(configured).expanduser().resolve()
    if database_url and database_url.startswith("sqlite"):
        raw_path = urlsplit(database_url).path
        if os.name == "nt" and raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
            raw_path = raw_path[1:]
        return Path(raw_path).expanduser().resolve().parent
    raise RuntimeError("app_data_directory_required_for_font_allowlist")


def _materialize_font_allowlist(font_bytes: bytes, digest: str, database_url: str | None) -> Path:
    target = _app_data_directory(database_url) / "font-embedding-allowlist" / "IBMPlexMono-Regular.ttf"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or _sha256(target.read_bytes()) != digest:
            raise RuntimeError("font_allowlist_digest_conflict")
        return target
    try:
        with target.open("xb") as handle:
            handle.write(font_bytes)
    except FileExistsError:
        if _sha256(target.read_bytes()) != digest:
            raise RuntimeError("font_allowlist_digest_conflict") from None
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy URL; defaults to the product APP_DATA_DIRECTORY database.",
    )
    parser.add_argument(
        "--owner-id",
        default=None,
        help="Existing product user UUID. If omitted, the active admin_slot=primary user is used.",
    )
    parser.add_argument(
        "--presentation-id",
        default=None,
        help="Optional UUID; defaults to a fresh UUID4.",
    )
    parser.add_argument(
        "--font-path",
        required=True,
        help="Actual IBM Plex Mono font bytes to persist.",
    )
    parser.add_argument(
        "--cjk-sans-font-path",
        default="C:/Windows/Fonts/malgun.ttf" if os.name == "nt" else None,
        help="Actual CJK sans face bytes; Windows Malgun Gothic is the default on Windows.",
    )
    parser.add_argument(
        "--cjk-serif-font-path",
        default="C:/Windows/Fonts/batang.ttc" if os.name == "nt" else None,
        help="Actual CJK serif face bytes; Windows Batang is the default on Windows.",
    )
    parser.add_argument(
        "--tenant-id",
        default="default",
        help="Semantic artifact tenant scope.",
    )
    return parser.parse_args()


def _html(slide: str) -> str:
    if slide == "a":
        return """<!doctype html><html data-qid='a-document' lang='ko'><head><meta charset='utf-8'><style>
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#f8fafc}
.panel{position:absolute;left:54px;top:46px;width:1172px;height:620px;border:2px solid #0f172a;border-radius:22px;background:#fff;padding:34px;font-family:'Malgun Gothic',sans-serif}
.simple-shape{position:absolute;border-radius:12px}.chart{position:absolute;left:780px;top:270px;width:370px;height:250px;background:linear-gradient(135deg,#e0f2fe,#bfdbfe);filter:drop-shadow(2px 4px 4px rgba(15,23,42,.18))}
.boundary-sentinel{position:absolute;background:transparent;border:1px dashed transparent}.image-card{position:absolute;left:82px;top:300px;width:260px;height:170px;object-fit:cover}
</style></head><body data-qid='a-body-root'><section data-qid='a-panel' class='panel' data-semantic-role='panel'>
<h1 data-qid='a-title' data-semantic-role='title' style='position:absolute;left:34px;top:28px;font-size:34px;color:#0f172a'>Authored 품질 Slice A</h1>
<p data-qid='a-body' data-semantic-role='body' style='position:absolute;left:38px;top:106px;width:650px;height:100px;font-size:25px;line-height:1.35;color:#1e293b'>혼합 텍스트 Korean / Latin / 2026 숫자 <span data-qid='a-body-mono' style='font-family:"IBM Plex Mono",monospace'>mono run 42</span></p>
<img data-qid='a-image' class='image-card' alt='simple image fixture' src='__IMAGE_DATA_URI__'>
<div data-qid='a-shape-primary' class='simple-shape' role='presentation' style='left:390px;top:310px;width:250px;height:72px;background:#2563eb'></div>
<div data-qid='a-shape-secondary' class='simple-shape' role='presentation' style='left:820px;top:430px;width:190px;height:56px;background:#f59e0b'></div>
<div data-qid='a-chart' class='chart complex-island' data-semantic-role='chart' aria-label='complex chart raster island'></div>
</section>
<div data-qid='a-right-sentinel' class='boundary-sentinel' style='left:1248px;top:310px;width:2px;height:10px;background:#ef4444;border-color:#ef4444'></div>
<div data-qid='a-bottom-sentinel' class='boundary-sentinel' style='left:640px;top:708px;width:10px;height:2px;background:#ef4444;border-color:#ef4444'></div>
</body></html>""".replace("__IMAGE_DATA_URI__", _IMAGE_DATA_URI)
    if slide == "b":
        return """<!doctype html><html data-qid='b-document' lang='ko'><head><meta charset='utf-8'><style>
*{box-sizing:border-box}html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#fff7ed}
.panel{position:absolute;left:72px;top:62px;width:1136px;height:596px;border:3px solid #7c2d12;border-radius:6px;background:#fffbeb;padding:28px;font-family:'Batang',serif}
.simple-shape{position:absolute;border-radius:2px}.chart{position:absolute;left:760px;top:250px;width:390px;height:272px;background:linear-gradient(45deg,#fed7aa,#fdba74);filter:drop-shadow(0 3px 5px rgba(124,45,18,.2))}
.boundary-sentinel{position:absolute;background:transparent;border:1px dashed transparent}.image-card{position:absolute;left:92px;top:290px;width:230px;height:190px;object-fit:contain}
</style></head><body data-qid='b-body-root'><section data-qid='b-panel' class='panel' data-semantic-role='panel'>
<h1 data-qid='b-title' data-semantic-role='title' style='position:absolute;left:28px;top:24px;font-size:32px;color:#7c2d12'>동일 class, 다른 내용 B</h1>
<p data-qid='b-body' data-semantic-role='body' style='position:absolute;left:32px;top:100px;width:640px;height:108px;font-size:23px;line-height:1.45;color:#431407'>다른 content / Latin-KR / 3.14159 <span data-qid='b-body-mono' style='font-family:"IBM Plex Mono",monospace'>fixed-width 007</span></p>
<img data-qid='b-image' class='image-card' alt='simple image fixture' src='__IMAGE_DATA_URI__'>
<div data-qid='b-shape-primary' class='simple-shape' role='presentation' style='left:370px;top:310px;width:270px;height:80px;background:#ea580c'></div>
<div data-qid='b-shape-secondary' class='simple-shape' role='presentation' style='left:840px;top:430px;width:160px;height:44px;background:#16a34a'></div>
<div data-qid='b-chart' class='chart complex-island' data-semantic-role='chart' aria-label='complex chart raster island'></div>
</section>
<div data-qid='b-right-sentinel' class='boundary-sentinel' style='left:1278px;top:300px;width:2px;height:12px;background:#ef4444;border-color:#ef4444'></div>
<div data-qid='b-bottom-sentinel' class='boundary-sentinel' style='left:620px;top:718px;width:12px;height:2px;background:#ef4444;border-color:#ef4444'></div>
</body></html>""".replace("__IMAGE_DATA_URI__", _IMAGE_DATA_URI)
    raise ValueError(f"unknown slide: {slide}")


def _bounds(x: float, y: float, width: float, height: float) -> dict[str, float]:
    # Authored DOM is captured in CSS px; semantic intent is persisted in
    # PowerPoint points using the fixed 96-DPI conversion used by the product.
    px_to_pt = 0.75
    return {
        "xPt": x * px_to_pt,
        "yPt": y * px_to_pt,
        "widthPt": width * px_to_pt,
        "heightPt": height * px_to_pt,
        "rotationDeg": 0,
    }


def _text_payload(text: str, *, title: bool = False, body: bool = False, font_family: str = "Malgun Gothic", color: str | None = None) -> dict:
    runs = []
    if body:
        runs.extend(
            [
                {
                    "text": text,
                    "language": "ko",
                    "script": "cjk",
                    "fontFamily": font_family,
                    "fontFace": "Regular",
                    "fontSizePt": 18,
                    "weight": 400,
                    "color": color or "#1e293b",
                },
            ]
        )
    else:
        runs.append(
            {
                "text": text,
                "language": "ko" if title else "en",
                "script": "cjk" if title else "latin",
                "fontFamily": font_family if title else "IBM Plex Mono",
                "fontFace": "Regular",
                "fontSizePt": 24 if title else 16,
                "weight": 700 if title else 400,
                "color": color or ("#0f172a" if title else "#1e293b"),
            }
        )
    return {"paragraphs": [{"runs": runs}], "whitespace": "normal"}


def _semantic_elements(slide: str, asset_digest: str) -> list[dict]:
    prefix = f"{slide}-"
    if slide == "a":
        title = "Authored 품질 Slice A"
        body = "혼합 텍스트 Korean / Latin / 2026 숫자"
    else:
        title = "동일 class, 다른 내용 B"
        body = "다른 content / Latin-KR / 3.14159"
    font_family = "Malgun Gothic" if slide == "a" else "Batang"
    text_color = "#0f172a" if slide == "a" else "#7c2d12"
    body_color = "#1e293b" if slide == "a" else "#431407"
    mono_text = "mono run 42" if slide == "a" else "fixed-width 007"
    root_children = [f"{prefix}body-root"]
    body_children = [
        f"{prefix}panel",
        f"{prefix}right-sentinel",
        f"{prefix}bottom-sentinel",
    ]
    children = [
        f"{prefix}title",
        f"{prefix}body",
        f"{prefix}image",
        f"{prefix}shape-primary",
        f"{prefix}shape-secondary",
        f"{prefix}chart",
    ]
    elements: list[dict] = [
        {
            "elementId": f"{prefix}document",
            "kind": "shape",
            "semanticRole": "documentRoot",
            "parentId": None,
            "childIds": root_children,
            "intendedOrder": 0,
            "intendedBounds": _bounds(0, 0, 1280, 720),
            "requiredFeatures": [],
            "payload": {"preset": "rectangle", "fill": "#ffffff", "stroke": None, "radiusPt": None},
        },
        {
            "elementId": f"{prefix}body-root",
            "kind": "shape",
            "semanticRole": "bodyRoot",
            "parentId": f"{prefix}document",
            "childIds": body_children,
            "intendedOrder": 1,
            "intendedBounds": _bounds(0, 0, 1280, 720),
            "requiredFeatures": [],
            "payload": {"preset": "rectangle", "fill": None, "stroke": None, "radiusPt": None},
        },
        {
            "elementId": f"{prefix}panel",
            "kind": "shape",
            "semanticRole": "panel",
            "parentId": f"{prefix}body-root",
            "childIds": children,
            "intendedOrder": 2,
            "intendedBounds": _bounds(54 if slide == "a" else 72, 46 if slide == "a" else 62, 1172 if slide == "a" else 1136, 620 if slide == "a" else 596),
            "requiredFeatures": [],
            "payload": {"preset": "roundedRectangle" if slide == "a" else "rectangle", "fill": "#ffffff", "stroke": "#0f172a" if slide == "a" else "#7c2d12", "radiusPt": 12 if slide == "a" else 2},
        },
        {
            "elementId": f"{prefix}title",
            "kind": "text",
            "semanticRole": "title",
            "parentId": f"{prefix}panel",
            "childIds": [],
            "intendedOrder": 3,
            "intendedBounds": _bounds(84, 78, 530, 52),
            "requiredFeatures": [],
            "payload": _text_payload(title, title=True, font_family=font_family, color=text_color),
        },
        {
            "elementId": f"{prefix}body",
            "kind": "text",
            "semanticRole": "body",
            "parentId": f"{prefix}panel",
            "childIds": [f"{prefix}body-mono"],
            "intendedOrder": 4,
            "intendedBounds": _bounds(86, 152, 620, 80),
            "requiredFeatures": [],
            "payload": _text_payload(body, body=True, font_family=font_family, color=body_color),
        },
        {
            "elementId": f"{prefix}body-mono",
            "kind": "text",
            "semanticRole": "bodyMonoRun",
            "parentId": f"{prefix}body",
            "childIds": [],
            "intendedOrder": 5,
            "intendedBounds": _bounds(530, 164, 180, 34),
            "requiredFeatures": [],
            "payload": _text_payload(mono_text, font_family="IBM Plex Mono", color=body_color),
        },
        {
            "elementId": f"{prefix}image",
            "kind": "image",
            "semanticRole": "image",
            "parentId": f"{prefix}panel",
            "childIds": [],
            "intendedOrder": 6,
            "intendedBounds": _bounds(136, 346, 220, 145),
            "requiredFeatures": [],
            "payload": {"assetId": "asset:vertical-slice-image", "assetDigest": asset_digest, "altText": "simple image fixture", "intrinsicWidthPx": 192, "intrinsicHeightPx": 128, "objectFit": "contain", "objectPosition": "50% 50%"},
        },
    ]
    for index, (qid, fill, x, y, width, height) in enumerate(
        [
            (f"{prefix}shape-primary", "#2563eb" if slide == "a" else "#ea580c", 390, 310, 250, 72),
            (f"{prefix}shape-secondary", "#f59e0b" if slide == "a" else "#16a34a", 820 if slide == "a" else 840, 430, 190 if slide == "a" else 160, 56 if slide == "a" else 44),
        ],
        start=7,
    ):
        elements.append(
            {
                "elementId": qid,
                "kind": "shape",
                "semanticRole": "simpleShape",
                "parentId": f"{prefix}panel",
                "childIds": [],
                "intendedOrder": index,
                "intendedBounds": _bounds(x, y, width, height),
                "requiredFeatures": [],
                "payload": {"preset": "roundedRectangle", "fill": fill, "stroke": None, "radiusPt": 8},
            }
        )
    elements.append(
        {
            "elementId": f"{prefix}chart",
            "kind": "complexIsland",
            "semanticRole": "chart",
            "parentId": f"{prefix}panel",
            "childIds": [],
            "intendedOrder": 9,
            "intendedBounds": _bounds(760 if slide == "b" else 780, 250 if slide == "b" else 270, 390 if slide == "b" else 370, 272 if slide == "b" else 250),
            "requiredFeatures": ["chart", "gradient", "filter"],
            "sourceRef": _sha256(f"{slide}:complex-chart-raster-island".encode()),
            "rasterReason": "complex_chart_requires_raster_island",
        }
    )
    for index, suffix in enumerate(("right-sentinel", "bottom-sentinel"), start=10):
        if slide == "a":
            sentinel_bounds = (1248, 310, 2, 10) if suffix.startswith("right") else (640, 708, 10, 2)
        else:
            sentinel_bounds = (1278, 300, 2, 12) if suffix.startswith("right") else (620, 718, 12, 2)
        elements.append(
            {
                "elementId": f"{prefix}{suffix}",
                "kind": "shape",
                "semanticRole": "boundarySentinel",
                "parentId": f"{prefix}body-root",
                "childIds": [],
                "intendedOrder": index,
                "intendedBounds": _bounds(*sentinel_bounds),
                "requiredFeatures": [],
                "payload": {"preset": "rectangle", "fill": None, "stroke": None, "radiusPt": None},
            }
        )
    return elements


def _semantic_draft(slide: str, asset_digest: str) -> dict:
    elements = _semantic_elements(slide, asset_digest)
    return {
        "semanticSource": {
            "schemaVersion": "authored-semantic-source/v1",
            "origin": "authored-declared-v1",
            "elements": elements,
            "elementIdentityRegistry": {
                "schemaVersion": "authored-element-identity-registry/v1",
                "activeQids": sorted(item["elementId"] for item in elements),
                "retiredQids": [],
            },
        }
    }


async def _seed(args: argparse.Namespace) -> dict:
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    owner_id = uuid.UUID(args.owner_id) if args.owner_id else None
    presentation_id = uuid.UUID(args.presentation_id) if args.presentation_id else uuid.uuid4()
    operation_id = uuid.uuid4()
    generation_attempt_id = uuid.uuid4()
    font_path = Path(args.font_path).expanduser().resolve()
    cjk_sans_path = Path(args.cjk_sans_font_path).expanduser().resolve() if args.cjk_sans_font_path else None
    cjk_serif_path = Path(args.cjk_serif_font_path).expanduser().resolve() if args.cjk_serif_font_path else None
    for label, candidate in (("font_path", font_path), ("cjk_sans_font_path", cjk_sans_path), ("cjk_serif_font_path", cjk_serif_path)):
        if candidate is None or not candidate.is_file() or candidate.stat().st_size == 0:
            raise SystemExit(f"{label}_actual_bytes_required:{candidate}")
    font_bytes = font_path.read_bytes()
    sans_font_bytes = cjk_sans_path.read_bytes()
    serif_font_bytes = cjk_serif_path.read_bytes()
    font_locator = str(font_path)
    image_locator = "bundled://templates/general/static/" + _IMAGE_PATH.name
    image_digest = _sha256(_PNG_BYTES)
    font_digest = _sha256(font_bytes)
    sans_font_digest = _sha256(sans_font_bytes)
    serif_font_digest = _sha256(serif_font_bytes)

    from sqlalchemy import select
    from services.database import async_session_maker, dispose_engines, sql_engine
    from models.sql.presentation import PresentationModel
    from models.sql.slide import SlideModel
    from models.sql.user import User
    from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
    from services.authored_vnext.semantic.canonical import canonical_digest
    from services.authored_vnext.semantic.publication import publish_authored_semantic_bundle_graph
    from models.authored_vnext.semantic.contracts import (
        ArtifactPointerV1,
        AssetManifestV1,
        FontManifestV1,
        ResolvedAssetV1,
        ResolvedFontV1,
    )
    from utils.owner_context import reset_current_owner_id, set_current_owner_id

    required_tables = {
        "authored_semantic_artifacts",
        "authored_emission_bundles",
        "authored_deck_emission_manifests",
        "authored_deck_emission_active",
    }
    from sqlalchemy import inspect
    async with sql_engine.connect() as connection:
        actual_tables = await connection.run_sync(lambda sync_connection: set(inspect(sync_connection).get_table_names()))
    missing_tables = sorted(required_tables - actual_tables)
    if missing_tables:
        raise RuntimeError("authored_semantic_schema_missing:" + ",".join(missing_tables))
    font_allowlist_path = _materialize_font_allowlist(
        font_bytes,
        font_digest,
        args.database_url or os.getenv("DATABASE_URL"),
    )
    if owner_id is None:
        async with async_session_maker() as session:
            owner = await session.scalar(
                select(User).where(
                    User.admin_slot == "primary",
                    User.is_active.is_(True),
                    User.deletion_state == "active",
                )
            )
            if owner is None:
                raise RuntimeError("primary_admin_owner_required")
            owner_id = owner.id
    else:
        async with async_session_maker() as session:
            if await session.get(User, owner_id) is None:
                raise RuntimeError("owner_user_not_found:" + str(owner_id))
    assert owner_id is not None
    owner_token = set_current_owner_id(owner_id)
    try:
        async with async_session_maker() as session:
            async with session.begin():
                existing = await session.scalar(select(PresentationModel.id).where(PresentationModel.id == presentation_id))
                if existing is not None:
                    raise RuntimeError("seed_presentation_already_exists")
                user = await session.get(User, owner_id)
                if user is None:
                    raise RuntimeError("owner_user_not_found:" + str(owner_id))
                presentation = PresentationModel(
                    id=presentation_id,
                    owner_id=owner_id,
                    content="Authored semantic dual-output deterministic vertical slice",
                    n_slides=2,
                    language="Korean",
                    title="Authored Semantic Vertical Slice",
                    mode="authored",
                    version="v1-standard",
                    lifecycle_status="staging",
                    revision=0,
                    theme={"mode": "authored", "seed": "semantic-vertical-slice-v1"},
                )
                session.add(presentation)
                await session.flush()
                conversation = WorkspaceConversation(owner_id=owner_id, presentation_id=presentation_id, next_position=2)
                session.add(conversation)
                await session.flush()
                request_payload = {"seed": "authored-semantic-vertical-slice-v1", "slideCount": 2, "template": "authored"}
                operation = WorkspaceOperation(
                    id=operation_id,
                    owner_id=owner_id,
                    owner_scope=str(owner_id),
                    conversation_id=conversation.id,
                    presentation_id=presentation_id,
                    anchor_position=1,
                    kind="presentation.generate",
                    state="running",
                    idempotency_key_hash=hashlib.sha256((str(presentation_id) + ":seed").encode()).hexdigest(),
                    request_hash=hashlib.sha256(json.dumps(request_payload, sort_keys=True).encode()).hexdigest(),
                    request_payload=request_payload,
                    expected_revision=0,
                    attempt_number=1,
                    attempt_token=str(generation_attempt_id),
                    fencing_token=1,
                )
                session.add(operation)
                htmls = [_html("a"), _html("b")]
                slides = []
                for index, html in enumerate(htmls):
                    slide = SlideModel(
                        id=uuid.uuid4(), owner_id=owner_id, presentation=presentation_id,
                        layout_group="authored", layout="authored:semantic-vertical-slice-v1",
                        index=index, content={"__authored__": True, "seed": "authored-semantic-vertical-slice-v1", "slide": index + 1},
                        html_content=html, properties={"seed": "authored-semantic-vertical-slice-v1"},
                    )
                    session.add(slide)
                    slides.append(slide)
                await session.flush()
                asset_pointer = ArtifactPointerV1(artifactRef=_artifact_ref(image_digest), digest=image_digest, byteLength=len(_PNG_BYTES), mediaType="image/png")
                font_pointer = ArtifactPointerV1(artifactRef=_artifact_ref(font_digest), digest=font_digest, byteLength=len(font_bytes), mediaType="font/ttf")
                sans_font_pointer = ArtifactPointerV1(artifactRef=_artifact_ref(sans_font_digest), digest=sans_font_digest, byteLength=len(sans_font_bytes), mediaType="font/ttf")
                serif_font_pointer = ArtifactPointerV1(artifactRef=_artifact_ref(serif_font_digest), digest=serif_font_digest, byteLength=len(serif_font_bytes), mediaType="font/collection")
                asset_manifest = AssetManifestV1(assets=[ResolvedAssetV1(
                    assetId="asset:vertical-slice-image", artifact=asset_pointer,
                    locatorDigest=_locator_digest(image_locator), resolverVersion="seed-resolver/v1",
                    widthPx=192, heightPx=128, provenance="authored", retentionClass="active",
                )])
                font_manifest = FontManifestV1(fonts=[
                    ResolvedFontV1(
                        faceId="font:ibm-plex-mono-regular", family="IBM Plex Mono", face="Regular",
                        artifact=font_pointer, locatorDigest=_locator_digest(font_locator), resolverVersion="seed-resolver/v1",
                        embeddingPermitted=True, subsettingPermitted=True, retentionClass="active",
                    ),
                    ResolvedFontV1(
                        faceId="font:malgun-gothic-regular", family="Malgun Gothic", face="Regular",
                        artifact=sans_font_pointer, locatorDigest=_locator_digest(str(cjk_sans_path)), resolverVersion="seed-resolver/v1",
                        embeddingPermitted=True, subsettingPermitted=True, retentionClass="active",
                    ),
                    ResolvedFontV1(
                        faceId="font:batang-regular", family="Batang", face="Regular",
                        artifact=serif_font_pointer, locatorDigest=_locator_digest(str(cjk_serif_path)), resolverVersion="seed-resolver/v1",
                        embeddingPermitted=True, subsettingPermitted=True, retentionClass="active",
                    ),
                ])
                master_digest = canonical_digest({"schemaVersion": "authored-master-contract/v1", "seed": "semantic-vertical-slice-v1"})
                composition_digest = canonical_digest({"schemaVersion": "authored-composition/v1", "seed": "semantic-vertical-slice-v1", "cssIsolation": "slide-root-v1"})
                manifest = await publish_authored_semantic_bundle_graph(
                    session,
                    presentation_id=presentation_id,
                    owner_id=owner_id,
                    expected_presentation_revision=0,
                    operation_id=operation_id,
                    generation_attempt_id=generation_attempt_id,
                    slides=slides,
                    htmls=htmls,
                    semantic_drafts=[_semantic_draft("a", image_digest), _semantic_draft("b", image_digest)],
                    master_contract_digest=master_digest,
                    composition_digest=composition_digest,
                    slide_asset_manifests=[asset_manifest, asset_manifest],
                    slide_font_manifests=[font_manifest, font_manifest],
                    resolved_artifact_bytes={image_digest: _PNG_BYTES, font_digest: font_bytes, sans_font_digest: sans_font_bytes, serif_font_digest: serif_font_bytes},
                    tenant_id=args.tenant_id,
                )
                presentation.revision = 1
                presentation.lifecycle_status = "published"
                operation.state = "succeeded"
                operation.result_revision = 1
                operation.attempt_token = None
                operation.result = {"manifestDigest": manifest.canonical_digest, "presentationRevision": 1, "slideCount": 2}
                operation.completed_at = datetime.now(timezone.utc)
                await session.flush()
                qids = [item["elementId"] for slide in ("a", "b") for item in _semantic_elements(slide, image_digest)]
                receipt = {
                    "schemaVersion": "authored-semantic-vertical-slice-seed/v1",
                    "presentationId": str(presentation_id),
                    "ownerId": str(owner_id),
                    "presentationRevision": 1,
                    "manifestDigest": manifest.canonical_digest,
                    "operationId": str(operation_id),
                    "generationAttemptId": str(generation_attempt_id),
                    "slideIds": [str(slide.id) for slide in slides],
                    "qids": qids,
                    "genericCssClass": "panel",
                    "assetDigest": image_digest,
                    "fontDigest": font_digest,
                    "fontDigests": [font_digest, sans_font_digest, serif_font_digest],
                    "fontSource": font_locator,
                    "fontAllowlistPath": str(font_allowlist_path),
                    "fontAllowlistDigest": font_digest,
                    "expectedRasterDemotionQids": [
                        "a-chart",
                        "a-shape-secondary",
                        "b-chart",
                        "b-shape-secondary",
                    ],
                    "promotionReady": False,
                    "fullNativeParityReady": False,
                    "independentVnextGenerationReady": False,
                }
            print(json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2))
            return receipt
    finally:
        reset_current_owner_id(owner_token)
        await dispose_engines()


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_seed(args))
    except Exception as error:
        raise SystemExit(f"authored_semantic_vertical_slice_seed_failed:{error}") from error


if __name__ == "__main__":
    main()
