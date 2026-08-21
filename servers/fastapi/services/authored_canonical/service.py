"""Durable canonical Authored document editing without Scene dependencies."""

from __future__ import annotations

from copy import deepcopy
from html.parser import HTMLParser
import hashlib
import uuid
from typing import Any, Iterable

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.authored_canonical_revision import AuthoredCanonicalRevision
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.presentation_export_attempt_service import sha256_canonical_json
from utils.authored_dom_quality import ensure_authored_qids
from utils.authored_patch_engine import (
    AuthoredPatchPlan,
    apply_authored_patch,
    inspect_authored_edit_capabilities,
)
from utils.datetime_utils import get_current_utc_datetime


SCHEMA_VERSION = "authored-canonical/v1"


class AuthoredCanonicalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AuthoredCanonicalConflict(AuthoredCanonicalError):
    def __init__(
        self,
        *,
        code: str,
        expected_revision: int,
        current_revision: int,
        current_digest: str,
    ) -> None:
        super().__init__(code, code)
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        self.current_digest = current_digest

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "expectedRevision": self.expected_revision,
            "currentRevision": self.current_revision,
            "currentDigest": self.current_digest,
            "retry": {
                "strategy": "refetch-and-reapply",
                "rebaseSupported": True,
            },
        }


class _SourceCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.assets: list[dict[str, str]] = []
        self.css_parts: list[str] = []
        self._style_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "style":
            self._style_depth += 1
        if tag.lower() == "img" and values.get("src"):
            source = values["src"]
            qid = values.get("data-qid", "")
            digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
            self.assets.append(
                {
                    "assetId": f"asset:{qid}:{digest[:12]}",
                    "qid": qid,
                    "source": source,
                    "sourceDigest": f"sha256:{digest}",
                    "digestKind": "locator",
                }
            )

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth:
            self.css_parts.append(data)


def _seal_slide(
    *,
    slide_id: str,
    index: int,
    slide_revision: int,
    html: str,
) -> dict[str, Any]:
    canonical_html = ensure_authored_qids(html)
    collector = _SourceCollector()
    collector.feed(canonical_html)
    collector.close()
    css_text = "\n".join(collector.css_parts)
    source = {
        "html": canonical_html,
        "cssText": css_text,
        "assetManifest": collector.assets,
    }
    return {
        "slideId": slide_id,
        "index": index,
        "slideRevision": slide_revision,
        **source,
        "sourceDigest": "sha256:" + sha256_canonical_json(source),
    }


def _deck_digest(slides: Iterable[dict[str, Any]]) -> str:
    sealed = [
        {
            "slideId": slide["slideId"],
            "index": slide["index"],
            "sourceDigest": slide["sourceDigest"],
            "assetManifest": slide["assetManifest"],
        }
        for slide in slides
    ]
    return "sha256:" + sha256_canonical_json(
        {"schemaVersion": SCHEMA_VERSION, "slides": sealed}
    )


def _response(row: AuthoredCanonicalRevision) -> dict[str, Any]:
    slides = deepcopy(row.snapshot["slides"])
    for slide in slides:
        slide["capabilities"] = inspect_authored_edit_capabilities(slide["html"])
    length = len(row.history)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "presentationId": str(row.presentation_id),
        "revision": row.revision,
        "sourceDigest": row.source_digest,
        "slides": slides,
        "history": {
            "position": row.history_position,
            "length": length,
            "canUndo": row.history_position > 0,
            "canRedo": row.history_position < length,
        },
    }


def _capability_by_qid(html: str) -> dict[str, dict[str, Any]]:
    return {
        item["qid"]: item for item in inspect_authored_edit_capabilities(html)
    }


def _required_capability(action: Any) -> str:
    return {
        "setText": "text",
        "setStyle": "style",
        "move": "position",
        "resize": "size",
        "crop": "crop",
        "replaceImage": "source",
    }[action.action]


class AuthoredCanonicalService:
    async def get_or_initialize(
        self,
        session: AsyncSession,
        presentation: PresentationModel,
    ) -> dict[str, Any]:
        row = await self._ensure_head(session, presentation)
        return _response(row)

    async def apply_patches(
        self,
        session: AsyncSession,
        presentation: PresentationModel,
        *,
        expected_revision: int,
        expected_digest: str | None,
        patches: list[dict[str, Any]],
        rebase_on_conflict: bool,
    ) -> dict[str, Any]:
        head = await self._ensure_head(session, presentation)
        original_expected_revision = expected_revision
        base = head
        rebasing = expected_revision != head.revision
        if not rebasing and expected_digest is not None and expected_digest != head.source_digest:
            raise self._conflict("authored_canonical_digest_conflict", expected_revision, head)
        if rebasing:
            if not rebase_on_conflict:
                raise self._conflict("authored_canonical_revision_conflict", expected_revision, head)
            base = await self._revision(session, presentation.id, expected_revision)
            if base is None:
                raise self._conflict("authored_canonical_rebase_base_unavailable", expected_revision, head)
            if expected_digest is not None and expected_digest != base.source_digest:
                raise self._conflict("authored_canonical_digest_conflict", expected_revision, head)

        try:
            next_snapshot, base_changes, current_changes = self._apply_patch_set(
                head.snapshot,
                patches,
                base_snapshot=base.snapshot if rebasing else None,
            )
        except AuthoredCanonicalError as error:
            if rebasing and error.code == "authored_canonical_rebase_conflict":
                raise self._conflict(error.code, expected_revision, head) from error
            raise
        if rebasing and base_changes != current_changes:
            raise self._conflict("authored_canonical_rebase_conflict", expected_revision, head)

        next_revision = head.revision + 1
        history = deepcopy(head.history[: head.history_position])
        history.append(
            {
                "beforeRevision": head.revision,
                "afterRevision": next_revision,
                "patchDigest": "sha256:" + sha256_canonical_json(patches),
            }
        )
        row = await self._commit_snapshot(
            session,
            presentation_id=presentation.id,
            expected_head=head,
            revision=next_revision,
            snapshot=next_snapshot,
            operation="patch",
            history=history,
            history_position=len(history),
        )
        result = _response(row)
        if rebasing:
            result["rebasedFromRevision"] = original_expected_revision
        return result

    async def history(
        self,
        session: AsyncSession,
        presentation: PresentationModel,
    ) -> dict[str, Any]:
        head = await self._ensure_head(session, presentation)
        rows = (
            await session.execute(
                select(AuthoredCanonicalRevision)
                .where(AuthoredCanonicalRevision.presentation_id == presentation.id)
                .order_by(AuthoredCanonicalRevision.revision)
            )
        ).scalars().all()
        return {
            "schemaVersion": "authored-canonical-history/v1",
            "presentationId": str(presentation.id),
            "revision": head.revision,
            "entries": [
                {
                    "revision": row.revision,
                    "parentRevision": row.parent_revision,
                    "operation": row.operation,
                    "sourceDigest": row.source_digest,
                    "historyPosition": row.history_position,
                    "createdAt": row.created_at.isoformat(),
                }
                for row in rows
            ],
        }

    async def undo(
        self,
        session: AsyncSession,
        presentation: PresentationModel,
        *,
        expected_revision: int,
        expected_digest: str | None,
    ) -> dict[str, Any]:
        head = await self._ensure_head(session, presentation)
        self._assert_expected(head, expected_revision, expected_digest)
        if head.history_position <= 0:
            raise AuthoredCanonicalError("authored_canonical_nothing_to_undo", "Nothing to undo")
        entry = head.history[head.history_position - 1]
        target = await self._revision(session, presentation.id, int(entry["beforeRevision"]))
        if target is None:
            raise AuthoredCanonicalError(
                "authored_canonical_history_unavailable", "Undo checkpoint is unavailable"
            )
        snapshot = self._restore_snapshot(head.snapshot, target.snapshot)
        row = await self._commit_snapshot(
            session,
            presentation_id=presentation.id,
            expected_head=head,
            revision=head.revision + 1,
            snapshot=snapshot,
            operation="undo",
            history=deepcopy(head.history),
            history_position=head.history_position - 1,
        )
        return _response(row)

    async def redo(
        self,
        session: AsyncSession,
        presentation: PresentationModel,
        *,
        expected_revision: int,
        expected_digest: str | None,
    ) -> dict[str, Any]:
        head = await self._ensure_head(session, presentation)
        self._assert_expected(head, expected_revision, expected_digest)
        if head.history_position >= len(head.history):
            raise AuthoredCanonicalError("authored_canonical_nothing_to_redo", "Nothing to redo")
        entry = head.history[head.history_position]
        target = await self._revision(session, presentation.id, int(entry["afterRevision"]))
        if target is None:
            raise AuthoredCanonicalError(
                "authored_canonical_history_unavailable", "Redo checkpoint is unavailable"
            )
        snapshot = self._restore_snapshot(head.snapshot, target.snapshot)
        row = await self._commit_snapshot(
            session,
            presentation_id=presentation.id,
            expected_head=head,
            revision=head.revision + 1,
            snapshot=snapshot,
            operation="redo",
            history=deepcopy(head.history),
            history_position=head.history_position + 1,
        )
        return _response(row)

    async def _ensure_head(
        self, session: AsyncSession, presentation: PresentationModel
    ) -> AuthoredCanonicalRevision:
        head = (
            await session.execute(
                select(AuthoredCanonicalRevision)
                .where(AuthoredCanonicalRevision.presentation_id == presentation.id)
                .order_by(AuthoredCanonicalRevision.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if head is not None and head.revision == presentation.revision:
            return head
        if head is not None and head.revision > presentation.revision:
            raise AuthoredCanonicalError(
                "authored_canonical_revision_ahead",
                "Canonical revision is ahead of the presentation revision",
            )

        slides = (
            await session.execute(
                select(SlideModel)
                .where(SlideModel.presentation == presentation.id)
                .order_by(SlideModel.index)
            )
        ).scalars().all()
        if not presentation.is_authored() or not slides or any(
            not slide.is_authored() or not slide.html_content for slide in slides
        ):
            raise AuthoredCanonicalError(
                "authored_canonical_source_unavailable",
                "Only complete Authored HTML decks have a canonical document",
            )
        previous_slides = {
            slide["slideId"]: slide for slide in (head.snapshot["slides"] if head else [])
        }
        sealed_slides = []
        for slide in slides:
            previous = previous_slides.get(str(slide.id))
            slide_revision = int(previous["slideRevision"]) if previous else 0
            if previous is not None and previous["html"] != slide.html_content:
                slide_revision += 1
            sealed_slides.append(
                _seal_slide(
                    slide_id=str(slide.id),
                    index=slide.index,
                    slide_revision=slide_revision,
                    html=slide.html_content,
                )
            )
        snapshot = {
            "schemaVersion": SCHEMA_VERSION,
            "presentationId": str(presentation.id),
            "slides": sealed_slides,
        }
        row = AuthoredCanonicalRevision(
            presentation_id=presentation.id,
            revision=presentation.revision,
            parent_revision=head.revision if head else presentation.revision,
            source_digest=_deck_digest(sealed_slides),
            operation="initialize",
            snapshot=snapshot,
            history=deepcopy(head.history) if head else [],
            history_position=head.history_position if head else 0,
        )
        sealed_by_id = {slide["slideId"]: slide for slide in sealed_slides}
        for slide in slides:
            canonical_slide = sealed_by_id[str(slide.id)]
            if slide.html_content != canonical_slide["html"]:
                slide.html_content = canonical_slide["html"]
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            concurrent = await self._revision(session, presentation.id, presentation.revision)
            if concurrent is None:
                raise
            return concurrent
        await session.refresh(row)
        return row

    def _apply_patch_set(
        self,
        snapshot: dict[str, Any],
        patches: list[dict[str, Any]],
        *,
        base_snapshot: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[list[dict[str, Any]]], list[list[dict[str, Any]]]]:
        if not patches:
            raise AuthoredCanonicalError("authored_canonical_empty_patch", "No patches supplied")
        result = deepcopy(snapshot)
        by_id = {slide["slideId"]: slide for slide in result["slides"]}
        base_by_id = (
            {slide["slideId"]: slide for slide in base_snapshot["slides"]}
            if base_snapshot is not None
            else by_id
        )
        seen: set[str] = set()
        base_changes: list[list[dict[str, Any]]] = []
        current_changes: list[list[dict[str, Any]]] = []
        for patch in patches:
            slide_id = str(patch["slideId"])
            if slide_id in seen:
                raise AuthoredCanonicalError(
                    "authored_canonical_duplicate_slide_patch",
                    "A slide may appear only once per atomic patch request",
                )
            seen.add(slide_id)
            current_slide = by_id.get(slide_id)
            base_slide = base_by_id.get(slide_id)
            if current_slide is None or base_slide is None:
                raise AuthoredCanonicalError(
                    "authored_canonical_slide_not_found", f"Unknown slide {slide_id}"
                )
            plan = AuthoredPatchPlan(strategy="patch", actions=patch["actions"])
            capabilities = _capability_by_qid(current_slide["html"])
            for action in plan.actions:
                capability = capabilities.get(action.qid)
                required = _required_capability(action)
                if (
                    capability is None
                    or not capability["editable"]
                    or required not in capability["fields"]
                ):
                    reason = capability["reason"] if capability else "target_not_found"
                    raise AuthoredCanonicalError(
                        "authored_canonical_read_only_target",
                        f"{action.qid} is read-only: {reason}",
                    )
            base_result = apply_authored_patch(base_slide["html"], plan)
            current_result = apply_authored_patch(current_slide["html"], plan)
            if not current_result.ok:
                raise AuthoredCanonicalError(
                    "authored_canonical_patch_rejected",
                    current_result.failure_reason or current_result.fallback_reason or "Patch rejected",
                )
            if not base_result.ok:
                raise AuthoredCanonicalError(
                    "authored_canonical_rebase_conflict",
                    base_result.failure_reason or "Patch no longer applies to its base",
                )
            base_changes.append(
                [change.before for change in base_result.changes]
            )
            current_changes.append(
                [change.before for change in current_result.changes]
            )
            sealed = _seal_slide(
                slide_id=slide_id,
                index=int(current_slide["index"]),
                slide_revision=int(current_slide["slideRevision"]) + 1,
                html=current_result.html,
            )
            current_slide.clear()
            current_slide.update(sealed)
        return result, base_changes, current_changes

    @staticmethod
    def _restore_snapshot(
        current: dict[str, Any], target: dict[str, Any]
    ) -> dict[str, Any]:
        restored = deepcopy(current)
        target_by_id = {slide["slideId"]: slide for slide in target["slides"]}
        for slide in restored["slides"]:
            prior = target_by_id.get(slide["slideId"])
            if prior is None:
                raise AuthoredCanonicalError(
                    "authored_canonical_history_shape_changed",
                    "Slide membership changed outside canonical history",
                )
            if prior["html"] != slide["html"]:
                replacement = _seal_slide(
                    slide_id=slide["slideId"],
                    index=int(slide["index"]),
                    slide_revision=int(slide["slideRevision"]) + 1,
                    html=prior["html"],
                )
                slide.clear()
                slide.update(replacement)
        return restored

    async def _commit_snapshot(
        self,
        session: AsyncSession,
        *,
        presentation_id: uuid.UUID,
        expected_head: AuthoredCanonicalRevision,
        revision: int,
        snapshot: dict[str, Any],
        operation: str,
        history: list[dict[str, Any]],
        history_position: int,
    ) -> AuthoredCanonicalRevision:
        changed = (
            await session.execute(
                update(PresentationModel)
                .where(
                    PresentationModel.id == presentation_id,
                    PresentationModel.revision == expected_head.revision,
                )
                .values(revision=revision, updated_at=get_current_utc_datetime())
            )
        ).rowcount
        if changed != 1:
            await session.rollback()
            latest = await self._latest(session, presentation_id)
            if latest is None:
                raise AuthoredCanonicalError(
                    "authored_canonical_source_unavailable", "Canonical head disappeared"
                )
            raise self._conflict(
                "authored_canonical_revision_conflict", expected_head.revision, latest
            )
        for slide in snapshot["slides"]:
            await session.execute(
                update(SlideModel)
                .where(
                    SlideModel.id == uuid.UUID(slide["slideId"]),
                    SlideModel.presentation == presentation_id,
                )
                .values(html_content=slide["html"])
            )
        row = AuthoredCanonicalRevision(
            presentation_id=presentation_id,
            revision=revision,
            parent_revision=expected_head.revision,
            source_digest=_deck_digest(snapshot["slides"]),
            operation=operation,
            snapshot=snapshot,
            history=history,
            history_position=history_position,
        )
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            latest = await self._latest(session, presentation_id)
            if latest is None:
                raise
            raise self._conflict(
                "authored_canonical_revision_conflict", expected_head.revision, latest
            )
        await session.refresh(row)
        return row

    async def _latest(
        self, session: AsyncSession, presentation_id: uuid.UUID
    ) -> AuthoredCanonicalRevision | None:
        return (
            await session.execute(
                select(AuthoredCanonicalRevision)
                .where(AuthoredCanonicalRevision.presentation_id == presentation_id)
                .order_by(AuthoredCanonicalRevision.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _revision(
        self,
        session: AsyncSession,
        presentation_id: uuid.UUID,
        revision: int,
    ) -> AuthoredCanonicalRevision | None:
        return (
            await session.execute(
                select(AuthoredCanonicalRevision).where(
                    AuthoredCanonicalRevision.presentation_id == presentation_id,
                    AuthoredCanonicalRevision.revision == revision,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    def _conflict(
        code: str,
        expected_revision: int,
        head: AuthoredCanonicalRevision,
    ) -> AuthoredCanonicalConflict:
        return AuthoredCanonicalConflict(
            code=code,
            expected_revision=expected_revision,
            current_revision=head.revision,
            current_digest=head.source_digest,
        )

    def _assert_expected(
        self,
        head: AuthoredCanonicalRevision,
        expected_revision: int,
        expected_digest: str | None,
    ) -> None:
        if expected_revision != head.revision:
            raise self._conflict(
                "authored_canonical_revision_conflict", expected_revision, head
            )
        if expected_digest is not None and expected_digest != head.source_digest:
            raise self._conflict(
                "authored_canonical_digest_conflict", expected_revision, head
            )


__all__ = [
    "AuthoredCanonicalConflict",
    "AuthoredCanonicalError",
    "AuthoredCanonicalService",
    "SCHEMA_VERSION",
]
