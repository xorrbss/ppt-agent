"""Seed one immutable export-only presentation into an already-migrated RC DB.

This is deliberately setup-only: the public v2 HTTP export, operation polling,
publication, and authorized download remain the evidence path.  A snapshot is
bound to the login principal selected by the RC launcher, so a foreign owner or
tenant cannot be substituted by this helper.
"""

from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping
import uuid

from sqlalchemy import select

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.user import User
from services.database import async_session_maker
from services.workspace_quality_export_operation_handlers import (
    _validated_authored_quality_receipt_digest,
)


_SCHEMA = "public-v2-export-snapshot/v1"


class SnapshotError(ValueError):
    pass


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "snapshotDigest"}
    return "sha256:" + sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SnapshotError(f"snapshot_{label}_invalid")
    return value


def _text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SnapshotError(f"snapshot_{label}_invalid")
    return value.strip()


def _sha256_prefixed(value: object, *, label: str) -> str:
    normalized = _text(value, label=label)
    if (
        not normalized.startswith("sha256:")
        or len(normalized) != 71
        or any(character not in "0123456789abcdef" for character in normalized[7:])
    ):
        raise SnapshotError(f"snapshot_{label}_invalid")
    return normalized


def _uuid(value: object, *, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(_text(value, label=label))
    except ValueError as error:
        raise SnapshotError(f"snapshot_{label}_invalid") from error


def _integer(value: object, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SnapshotError(f"snapshot_{label}_invalid")
    return value


def _load_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotError("snapshot_unreadable") from error
    snapshot = _mapping(raw, label="root")
    if snapshot.get("schemaVersion") != _SCHEMA:
        raise SnapshotError("snapshot_schema_invalid")
    expected = _sha256_prefixed(snapshot.get("snapshotDigest"), label="digest")
    actual = _canonical_digest(snapshot)
    if expected != actual:
        raise SnapshotError("snapshot_digest_mismatch")
    return snapshot, actual


def _slides_digest(slides: list[SlideModel]) -> str:
    return sha256(
        json.dumps(
            [
                {
                    "id": str(slide.id),
                    "index": slide.index,
                    "content": slide.content,
                    "htmlContent": slide.html_content,
                    "speakerNote": slide.speaker_note,
                    "properties": slide.properties,
                }
                for slide in sorted(slides, key=lambda item: (item.index, str(item.id)))
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _theme(
    *, snapshot_digest: str, scene_digest: str, receipt: dict[str, Any], slides_digest: str
) -> dict[str, Any]:
    # Keep the smallest product projection that the isolated fidelity export
    # requires. The sealed receipt is validated below rather than synthesized.
    return {
        "mode": "authored",
        "authored_vnext": {"sceneDigest": scene_digest},
        "authoredQualityGate": {"qualityReceiptV3": receipt},
        "publicV2ExportSnapshot": {
            "schemaVersion": _SCHEMA,
            "digest": snapshot_digest,
            "slidesDigest": slides_digest,
        },
    }


def _slide_model(*, owner_id: uuid.UUID, presentation_id: uuid.UUID, raw: Mapping[str, Any]) -> SlideModel:
    index = _integer(raw.get("index"), label="slide_index")
    html = _text(raw.get("htmlContent"), label="slide_html")
    content = raw.get("content")
    if not isinstance(content, dict) or content.get("__authored__") is not True:
        raise SnapshotError("snapshot_slide_content_invalid")
    properties = raw.get("properties")
    if properties is not None and not isinstance(properties, dict):
        raise SnapshotError("snapshot_slide_properties_invalid")
    speaker_note = raw.get("speakerNote")
    if speaker_note is not None and not isinstance(speaker_note, str):
        raise SnapshotError("snapshot_slide_speaker_note_invalid")
    return SlideModel(
        id=_uuid(raw.get("id"), label="slide_id"),
        owner_id=owner_id,
        presentation=presentation_id,
        layout_group="authored",
        layout="authored:export-snapshot",
        index=index,
        content=content,
        html_content=html,
        speaker_note=speaker_note,
        properties=properties,
    )


async def _seed(snapshot: dict[str, Any], *, username: str) -> dict[str, Any]:
    principal = _mapping(snapshot.get("principal"), label="principal")
    if _text(principal.get("username"), label="principal_username") != username:
        raise SnapshotError("snapshot_principal_username_mismatch")
    tenant_id = _text(principal.get("tenantId"), label="principal_tenant")
    if tenant_id != (os.getenv("PPT_SERVICE_TENANT_ID") or "default").strip():
        raise SnapshotError("snapshot_principal_tenant_mismatch")

    presentation = _mapping(snapshot.get("presentation"), label="presentation")
    presentation_id = _uuid(presentation.get("id"), label="presentation_id")
    revision = _integer(presentation.get("revision"), label="presentation_revision")
    n_slides = _integer(presentation.get("nSlides"), label="presentation_slide_count", minimum=1)
    raw_slides = snapshot.get("slides")
    if not isinstance(raw_slides, list) or len(raw_slides) != n_slides:
        raise SnapshotError("snapshot_slide_count_mismatch")
    slides = [_slide_model(owner_id=uuid.UUID(int=0), presentation_id=presentation_id, raw=_mapping(value, label="slide")) for value in raw_slides]
    if sorted(slide.index for slide in slides) != list(range(n_slides)):
        raise SnapshotError("snapshot_slide_indices_invalid")

    quality = _mapping(snapshot.get("quality"), label="quality")
    scene_digest = _sha256_prefixed(quality.get("sceneDigest"), label="scene_digest")
    receipt = _mapping(quality.get("receipt"), label="quality_receipt")
    if _integer(receipt.get("revision"), label="quality_revision") != revision:
        raise SnapshotError("snapshot_quality_revision_mismatch")
    snapshot_digest = _sha256_prefixed(snapshot.get("snapshotDigest"), label="digest")
    slides_digest = _slides_digest(slides)
    theme = _theme(
        snapshot_digest=snapshot_digest,
        scene_digest=scene_digest,
        receipt=receipt,
        slides_digest=slides_digest,
    )
    # Reuse the production validation rather than accepting a fixture PASS.
    _validated_authored_quality_receipt_digest(theme)

    async with async_session_maker() as session:
        user = await session.scalar(select(User).where(User.username == username))
        if user is None or not user.is_active:
            raise SnapshotError("snapshot_principal_not_bootstrapped")
        for slide in slides:
            slide.owner_id = user.id

        existing = await session.get(PresentationModel, presentation_id)
        if existing is not None:
            binding = (
                existing.theme.get("publicV2ExportSnapshot", {})
                if isinstance(existing.theme, dict)
                else {}
            )
            existing_digest = binding.get("digest") if isinstance(binding, dict) else None
            existing_slides_digest = binding.get("slidesDigest") if isinstance(binding, dict) else None
            if (
                existing.owner_id != user.id
                or existing.revision != revision
                or existing_digest != snapshot_digest
                or existing_slides_digest != slides_digest
            ):
                raise SnapshotError("snapshot_presentation_conflict")
            stored = list(
                (await session.scalars(select(SlideModel).where(SlideModel.presentation == presentation_id))).all()
            )
            if (
                len(stored) != n_slides
                or any(slide.owner_id != user.id for slide in stored)
                or _slides_digest(stored) != slides_digest
            ):
                raise SnapshotError("snapshot_presentation_conflict")
        else:
            created = PresentationModel(
                id=presentation_id,
                owner_id=user.id,
                content=_text(presentation.get("content"), label="presentation_content"),
                n_slides=n_slides,
                language=_text(presentation.get("language"), label="presentation_language"),
                title=(presentation.get("title") if isinstance(presentation.get("title"), str) else None),
                mode="authored",
                lifecycle_status="published",
                revision=revision,
                theme=theme,
            )
            session.add(created)
            session.add_all(slides)
            await session.commit()

    return {
        "schemaVersion": "public-v2-export-snapshot-receipt/v1",
        "presentationId": str(presentation_id),
        "sourceRevision": revision,
        "ownerId": str(user.id),
        "tenantId": tenant_id,
        "snapshotDigest": snapshot_digest,
        "sceneDigest": scene_digest,
        "qualityDigest": _validated_authored_quality_receipt_digest(theme),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    try:
        snapshot, _digest = _load_snapshot(args.snapshot)
        receipt = asyncio.run(_seed(snapshot, username=args.username.strip()))
    except SnapshotError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
