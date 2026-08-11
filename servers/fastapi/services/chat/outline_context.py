"""Deck-outline, template-context, and source-document helpers for Chat.

This intentionally stays outside :mod:`memory_layer`: the memory layer remains a
small persistence facade, while this module owns the bounded, write-capable
operations that a Chat tool exposes.  ``ChatTools`` can inject one instance per
presentation without changing the existing tool names or storage contracts.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import uuid
from typing import Any, Protocol

from sqlmodel import select

from utils.owner_context import get_current_owner_id, get_current_owner_is_admin
from constants.presentation import (
    AUTHORED_SLIDE_CHAT_EDIT_REFUSAL,
    AUTHORED_SLIDE_CHAT_EDIT_REFUSAL_CODE,
    MAX_NUMBER_OF_SLIDES,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from services.chat.pending_slides import reconcile_pending_slides
from services.documents_loader import DocumentsLoader
from services.mem0_presentation_memory_service import MEM0_PRESENTATION_MEMORY_SERVICE
from services.references import ReferenceProviderError, admit_reference_snapshots
from services.temp_file_service import TEMP_FILE_SERVICE
from templates.v2.catalog import (
    get_template_v2_catalog_entry,
    is_template_v2_default_release,
)

LOGGER = logging.getLogger(__name__)

BLANK_SLIDE_LAYOUT_ID = "__blank_slide__"
DEFAULT_SOURCE_DOCUMENT_CHARS = 12_000
MIN_SOURCE_DOCUMENT_CHARS = 1_000
MAX_SOURCE_DOCUMENT_CHARS = 30_000
MAX_BLOCK_CONTENT_CHARS = 75_000


class PresentationContextStore(Protocol):
    """The small existing memory-layer surface used by these helpers."""

    async def get(self, key: str) -> Any: ...

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]: ...

    async def get_available_layouts(self) -> list[dict[str, Any]]: ...

    async def get_content_schema_from_layout_id(
        self, layout_id: str
    ) -> dict[str, Any] | None: ...

    async def save_slide(
        self,
        *,
        content: dict[str, Any],
        layout_id: str,
        index: int,
        replace_old_slide_at_index: bool,
    ) -> dict[str, Any]: ...


class OutlineTemplateContext:
    """Safe, presentation-scoped operations used by outline/context Chat tools.

    The API is deliberately JSON-shaped.  It gives the tool registry a stable
    adapter while keeping all persistence and source-file checks testable without
    coupling Chat handlers to a multi-thousand-line storage class.
    """

    def __init__(
        self,
        *,
        sql_session: Any,
        presentation_id: uuid.UUID,
        memory: PresentationContextStore,
    ) -> None:
        self._sql_session = sql_session
        self._presentation_id = presentation_id
        self._memory = memory

    async def get_outline_draft(self) -> dict[str, Any]:
        presentation = await self._presentation()
        if presentation is None:
            return self._not_found()
        slides = self._outline_slides(presentation.outlines)
        return self._outline_response(slides)

    async def add_outline(self, *, content: str, index: int | None = None) -> dict[str, Any]:
        presentation = await self._presentation()
        if presentation is None:
            return self._not_found()
        normalized = self._normalize_outline_content(content)
        if normalized is None:
            return {"found": True, "error": "outline_content_required"}

        slides = self._outline_slides(presentation.outlines)
        if len(slides) >= MAX_NUMBER_OF_SLIDES:
            return {
                "found": True,
                "error": "max_slides_reached",
                "max_slides": MAX_NUMBER_OF_SLIDES,
            }
        target = len(slides) if index is None else max(0, min(index, len(slides)))
        slides.insert(target, normalized)
        await self._save_outline(presentation, slides)
        return {**self._outline_response(slides), "inserted_index": target}

    async def update_outline(self, *, index: int, content: str) -> dict[str, Any]:
        presentation = await self._presentation()
        if presentation is None:
            return self._not_found()
        normalized = self._normalize_outline_content(content)
        if normalized is None:
            return {"found": True, "error": "outline_content_required"}
        slides = self._outline_slides(presentation.outlines)
        if index < 0 or index >= len(slides):
            return {"found": True, "error": "outline_index_not_found", "index": index}
        slides[index] = normalized
        await self._save_outline(presentation, slides)
        return {**self._outline_response(slides), "updated_index": index}

    async def delete_outline(self, *, index: int) -> dict[str, Any]:
        presentation = await self._presentation()
        if presentation is None:
            return self._not_found()
        slides = self._outline_slides(presentation.outlines)
        if index < 0 or index >= len(slides):
            return {"found": True, "error": "outline_index_not_found", "index": index}
        removed = slides.pop(index)
        await self._save_outline(presentation, slides)
        return {**self._outline_response(slides), "deleted_index": index, "deleted": removed}

    async def move_outline(self, *, from_index: int, to_index: int) -> dict[str, Any]:
        presentation = await self._presentation()
        if presentation is None:
            return self._not_found()
        slides = self._outline_slides(presentation.outlines)
        if from_index < 0 or from_index >= len(slides):
            return {"found": True, "error": "outline_index_not_found", "index": from_index}
        if to_index < 0 or to_index >= len(slides):
            return {"found": True, "error": "outline_index_not_found", "index": to_index}
        if from_index != to_index:
            item = slides.pop(from_index)
            slides.insert(to_index, item)
            await self._save_outline(presentation, slides)
        return {
            **self._outline_response(slides),
            "moved_from_index": from_index,
            "moved_to_index": to_index,
        }

    async def add_new_slide(self, *, index: int | None = None) -> dict[str, Any]:
        """Create a conventional blank slide without manufacturing native UI.

        Template V2 callers should use ``add_new_slide_layout`` so hydration owns
        canonical native UI.  A blank slide intentionally has neither ``ui`` nor
        ``html_content`` and therefore preserves the mutually-exclusive contract.
        """
        presentation = await self._presentation()
        if presentation is None:
            return self._not_found()
        slides = await self._slides()
        if len(slides) >= MAX_NUMBER_OF_SLIDES:
            return {
                "found": True,
                "error": "max_slides_reached",
                "max_slides": MAX_NUMBER_OF_SLIDES,
            }

        if any(slide.is_authored() for slide in slides):
            # A blank slide here can never be filled: `save_slide` has no layout
            # to author against in an authored deck, so the insert would only
            # add an empty page beside the authored ones.
            return {
                "found": True,
                # No `index` accompanies this one: the refused call inserts a
                # new page, so there is no existing page for a remake to target.
                "error": AUTHORED_SLIDE_CHAT_EDIT_REFUSAL_CODE,
                "message": AUTHORED_SLIDE_CHAT_EDIT_REFUSAL,
            }

        target = len(slides) if index is None else max(0, min(index, len(slides)))
        previous_count = presentation.n_slides
        shifted = [slide for slide in slides if slide.index >= target]
        new_slide = SlideModel(
            presentation=self._presentation_id,
            layout_group=self._resolve_layout_group(presentation),
            layout=BLANK_SLIDE_LAYOUT_ID,
            index=target,
            content={},
            html_content=None,
            ui=None,
            speaker_note="",
            properties=None,
        )
        try:
            # Shift through the unit of work rather than a bulk UPDATE: the
            # statement would execute immediately and hold the writer lock for
            # the rest of the turn.  `ix_slides_presentation` is non-unique, so
            # a transient duplicate index before flush violates no constraint.
            for slide in shifted:
                slide.index += 1
                self._sql_session.add(slide)
            self._sql_session.add(new_slide)
            presentation.n_slides = len(slides) + 1
            self._sql_session.add(presentation)
            await self._sql_session.commit()
            await self._maybe_refresh(new_slide)
        except Exception:
            for slide in shifted:
                slide.index -= 1
            presentation.n_slides = previous_count
            await self._maybe_rollback()
            raise
        return {
            "found": True,
            "created": True,
            "index": target,
            "slide_id": str(new_slide.id),
            "layout_id": BLANK_SLIDE_LAYOUT_ID,
        }

    async def add_new_slide_layout(
        self, *, content: dict[str, Any], layout_id: str, index: int | None = None
    ) -> dict[str, Any]:
        """Append/insert via the canonical save path (including V2 hydration)."""
        presentation = await self._presentation()
        if presentation is None:
            return self._not_found()
        slides = await self._slides()
        if len(slides) >= MAX_NUMBER_OF_SLIDES:
            return {
                "found": True,
                "error": "max_slides_reached",
                "max_slides": MAX_NUMBER_OF_SLIDES,
            }
        target = len(slides) if index is None else max(0, min(index, len(slides)))
        return await self._memory.save_slide(
            content=content,
            layout_id=layout_id,
            index=target,
            replace_old_slide_at_index=False,
        )

    async def get_template_summary(self) -> dict[str, Any]:
        presentation = await self._presentation()
        if presentation is None:
            return self._not_found()
        outline = self._outline_slides(presentation.outlines)
        layouts = await self._memory.get_available_layouts()
        templates = await self._template_records(presentation)
        return {
            "found": True,
            "presentation_id": str(presentation.id),
            "title": presentation.title,
            "mode": presentation.mode,
            "version": presentation.version,
            "outline": self._outline_response(outline),
            "layouts": layouts,
            "template_v2": [
                {
                    "id": record.id,
                    "name": record.name,
                    "description": record.description,
                    "revision": record.revision,
                }
                for record in templates
            ],
        }

    async def get_available_blocks(
        self,
        *,
        query: str | None = None,
        layout_id: str | None = None,
        element_type: str | None = None,
        block_id: str | None = None,
        include_full_content: bool = False,
        max_results: int = 30,
    ) -> dict[str, Any]:
        """Return components only from templates associated with this presentation."""
        presentation = await self._presentation()
        if presentation is None:
            return self._not_found()
        query_lower = (query or "").strip().lower()
        block_filter = (block_id or "").strip()
        layout_filter = (layout_id or "").strip()
        type_filter = (element_type or "").strip().lower()
        try:
            limit = max(1, min(int(max_results), 100))
        except (TypeError, ValueError):
            limit = 30
        blocks: list[dict[str, Any]] = []
        for template in await self._template_records(presentation):
            for candidate in self._template_components(template):
                component = candidate["component"]
                component_id = str(component.get("id") or "")
                candidate_layout_id = str(candidate.get("layout_id") or "")
                types = self._element_types(component)
                haystack = " ".join(
                    (
                        component_id,
                        str(component.get("name") or ""),
                        str(component.get("description") or ""),
                        " ".join(types),
                    )
                ).lower()
                if block_filter and component_id != block_filter:
                    continue
                if layout_filter and candidate_layout_id != layout_filter:
                    continue
                if type_filter and type_filter not in types:
                    continue
                if query_lower and query_lower not in haystack:
                    continue
                item: dict[str, Any] = {
                    "id": component_id,
                    "name": component.get("name"),
                    "description": component.get("description"),
                    "template_id": template.id,
                    "layout_id": candidate_layout_id or None,
                    "element_types": types,
                }
                if include_full_content:
                    full_component = copy.deepcopy(component)
                    try:
                        is_too_large = (
                            len(json.dumps(full_component, ensure_ascii=False, default=str))
                            > MAX_BLOCK_CONTENT_CHARS
                        )
                    except (TypeError, ValueError):
                        is_too_large = True
                    if is_too_large:
                        item["content_omitted"] = True
                    else:
                        item["component"] = full_component
                blocks.append(item)
                if len(blocks) >= limit:
                    return {"found": True, "blocks": blocks, "truncated": True}
        return {"found": True, "blocks": blocks, "truncated": False}

    async def read_source_documents(
        self, *, query: str | None = None, max_chars: int = DEFAULT_SOURCE_DOCUMENT_CHARS
    ) -> dict[str, Any]:
        """Read only owner-scoped uploaded files, with a bounded response budget."""
        presentation = await self._presentation()
        if presentation is None:
            return self._not_found()
        budget = self._source_char_budget(max_chars)
        paths = presentation.file_paths if isinstance(presentation.file_paths, list) else []
        documents: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        remaining = budget
        if presentation.reference_snapshot_ids:
            try:
                snapshots = await admit_reference_snapshots(
                    self._sql_session,
                    snapshot_ids=[
                        uuid.UUID(str(snapshot_id))
                        for snapshot_id in presentation.reference_snapshot_ids
                    ],
                )
                for snapshot in snapshots:
                    values = snapshot.payload().get("documents")
                    if not isinstance(values, list):
                        raise ReferenceProviderError(
                            "reference_snapshot_payload_invalid",
                            status_code=409,
                        )
                    for index, value in enumerate(values):
                        if remaining <= 0:
                            break
                        if not isinstance(value, dict) or not isinstance(
                            value.get("text"), str
                        ):
                            raise ReferenceProviderError(
                                "reference_snapshot_payload_invalid",
                                status_code=409,
                            )
                        text = value["text"]
                        excerpt = text[:remaining]
                        remaining -= len(excerpt)
                        documents.append(
                            {
                                "name": str(
                                    value.get("name") or f"document-{index + 1}"
                                ),
                                "content": excerpt,
                                "truncated": len(excerpt) < len(text),
                            }
                        )
            except (ReferenceProviderError, ValueError):
                LOGGER.warning(
                    "Chat canonical source-document read rejected/failed "
                    "(presentation_id=%s)",
                    self._presentation_id,
                    exc_info=True,
                )
                errors.append({"name": "uploaded-document", "error": "unavailable"})

        for source_path in paths:
            if not isinstance(source_path, str) or remaining <= 0:
                break
            try:
                # This validates traversal, existence and the current owner's root
                # before the loader is allowed to open a file.
                resolved = TEMP_FILE_SERVICE.resolve_temp_path(source_path, must_exist=True)
                loader = DocumentsLoader([resolved], presentation.language)
                await loader.load_documents(load_text=True, load_images=False)
                text = loader.documents[0] if loader.documents else ""
                if not isinstance(text, str):
                    text = str(text)
                excerpt = text[:remaining]
                remaining -= len(excerpt)
                documents.append(
                    {
                        "name": os.path.basename(resolved),
                        "content": excerpt,
                        "truncated": len(excerpt) < len(text),
                    }
                )
            except Exception:
                LOGGER.warning(
                    "Chat source-document read rejected/failed (presentation_id=%s)",
                    self._presentation_id,
                    exc_info=True,
                )
                # Do not echo absolute paths or parser details to the model.
                errors.append({"name": os.path.basename(source_path), "error": "unavailable"})

        if not documents and query and query.strip():
            try:
                context = await MEM0_PRESENTATION_MEMORY_SERVICE.retrieve_context(
                    presentation_id=str(self._presentation_id), query=query.strip()
                )
                if context:
                    documents.append(
                        {
                            "name": "presentation-memory",
                            "content": str(context)[:budget],
                            "truncated": len(str(context)) > budget,
                        }
                    )
            except Exception:
                LOGGER.warning(
                    "Chat source-document memory fallback failed (presentation_id=%s)",
                    self._presentation_id,
                    exc_info=True,
                )
        return {
            "found": True,
            "documents": documents,
            "errors": errors,
            "truncated": remaining <= 0,
        }

    async def search_slide(self, *, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Singular upstream-compatible alias for the local ``searchSlides`` tool."""
        return await self._memory.search(query, limit)

    async def get_available_layouts(self) -> list[dict[str, Any]]:
        return await self._memory.get_available_layouts()

    async def get_content_schema_from_layout_id(self, layout_id: str) -> dict[str, Any] | None:
        return await self._memory.get_content_schema_from_layout_id(layout_id)

    async def _presentation(self) -> PresentationModel | None:
        presentation = await self._sql_session.get(PresentationModel, self._presentation_id)
        if presentation is None or not self._is_owner_scoped(presentation):
            return None
        return presentation

    async def _slides(self) -> list[SlideModel]:
        result = await self._sql_session.scalars(
            select(SlideModel)
            .where(SlideModel.presentation == self._presentation_id)
            .order_by(SlideModel.index)
        )
        return [
            slide
            for slide in reconcile_pending_slides(
                self._sql_session, self._presentation_id, result
            )
            if self._is_owner_scoped(slide)
        ]

    async def _save_outline(self, presentation: PresentationModel, slides: list[str]) -> None:
        original_outlines = copy.deepcopy(presentation.outlines)
        original_count = presentation.n_slides
        original_title = presentation.title
        try:
            presentation.outlines = {"slides": [{"content": item} for item in slides]}
            presentation.n_slides = len(slides)
            presentation.title = self._outline_title(slides) or presentation.title
            self._sql_session.add(presentation)
            await self._sql_session.commit()
        except Exception:
            presentation.outlines = original_outlines
            presentation.n_slides = original_count
            presentation.title = original_title
            await self._maybe_rollback()
            raise
        try:
            await MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines(
                self._presentation_id, presentation.outlines
            )
        except Exception:
            # SQL is authoritative. A memory-index outage must not turn a durable
            # outline edit into a false failure after it has committed.
            LOGGER.warning(
                "Chat outline memory indexing failed (presentation_id=%s)",
                self._presentation_id,
                exc_info=True,
            )

    async def _template_records(self, presentation: PresentationModel) -> list[TemplateV2]:
        result = await self._sql_session.scalars(
            select(TemplateV2)
            .join(
                TemplateV2LocalState,
                TemplateV2LocalState.template_id == TemplateV2.id,
            )
            .where(TemplateV2LocalState.presentation_id == presentation.id)
            .order_by(TemplateV2.updated_at.desc())
        )
        records = list(result)
        records = [record for record in records if self._is_template_scoped(record)]
        if records:
            return records

        # Transitional rows can still carry presentation_id until their sidecar is
        # backfilled.  Do not list arbitrary default templates as a fallback.
        legacy = await self._sql_session.scalars(
            select(TemplateV2)
            .where(TemplateV2.presentation_id == presentation.id)
            .order_by(TemplateV2.updated_at.desc())
        )
        return [record for record in legacy if self._is_template_scoped(record)]

    @staticmethod
    def _template_components(template: TemplateV2) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        payloads = (template.components, template.merged_components, template.layouts, template.raw_layouts)
        seen: set[tuple[str, str]] = set()
        for payload in payloads:
            for component, layout_id in _walk_components(payload):
                component_id = str(component.get("id") or "")
                if not component_id:
                    continue
                key = (component_id, layout_id or "")
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({"component": component, "layout_id": layout_id})
        return candidates

    @staticmethod
    def _element_types(component: dict[str, Any]) -> list[str]:
        types: set[str] = set()

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                element_type = value.get("type")
                if isinstance(element_type, str):
                    types.add(element_type.lower())
                for child_key in ("elements", "children", "child"):
                    visit(value.get(child_key))
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(component.get("elements"))
        return sorted(types)

    @staticmethod
    def _outline_slides(value: Any) -> list[str]:
        if not isinstance(value, dict) or not isinstance(value.get("slides"), list):
            return []
        result: list[str] = []
        for item in value["slides"]:
            content = item.get("content") if isinstance(item, dict) else item
            normalized = OutlineTemplateContext._normalize_outline_content(content)
            if normalized is not None:
                result.append(normalized)
        return result

    @staticmethod
    def _normalize_outline_content(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 20_000:
            return None
        return normalized

    @staticmethod
    def _outline_title(slides: list[str]) -> str | None:
        if not slides:
            return None
        first_line = next((line.strip() for line in slides[0].splitlines() if line.strip()), "")
        title = re.sub(r"^#{1,6}\s*", "", first_line).strip()
        return title[:200] or None

    @staticmethod
    def _outline_response(slides: list[str]) -> dict[str, Any]:
        return {
            "found": True,
            "slide_count": len(slides),
            "slides": [
                {"index": index, "slide_number": index + 1, "content": content}
                for index, content in enumerate(slides)
            ],
        }

    @staticmethod
    def _resolve_layout_group(presentation: PresentationModel) -> str:
        layout = presentation.layout if isinstance(presentation.layout, dict) else {}
        name = layout.get("name")
        return name if isinstance(name, str) and name.strip() else "presentation"

    @staticmethod
    def _source_char_budget(value: int) -> int:
        if not isinstance(value, int):
            return DEFAULT_SOURCE_DOCUMENT_CHARS
        return max(MIN_SOURCE_DOCUMENT_CHARS, min(value, MAX_SOURCE_DOCUMENT_CHARS))

    async def _maybe_rollback(self) -> None:
        rollback = getattr(self._sql_session, "rollback", None)
        if rollback is not None:
            await rollback()

    async def _maybe_refresh(self, value: Any) -> None:
        refresh = getattr(self._sql_session, "refresh", None)
        if refresh is not None:
            await refresh(value)

    @staticmethod
    def _is_owner_scoped(value: Any) -> bool:
        """Mirror request tenant scope for ``get`` and test-double sessions.

        Production SQL sessions apply this predicate globally, but ``get`` and
        lightweight unit sessions can bypass that event.  Admins remain the only
        cross-owner readers; an unauthenticated caller can see legacy ownerless
        rows only.
        """
        if get_current_owner_is_admin():
            return True
        return getattr(value, "owner_id", None) == get_current_owner_id()

    @staticmethod
    def _is_template_scoped(template: TemplateV2) -> bool:
        if (
            get_template_v2_catalog_entry(template.id) is not None
            or (template.owner_id is None and template.is_default)
        ):
            return is_template_v2_default_release(template.id, template.assets)
        if get_current_owner_is_admin():
            return True
        return template.owner_id == get_current_owner_id()

    @staticmethod
    def _not_found() -> dict[str, Any]:
        return {"found": False, "error": "presentation_not_found"}


def _walk_components(payload: Any, layout_id: str | None = None):
    """Yield component dictionaries from known Template V2 JSON shapes only."""
    if isinstance(payload, list):
        for item in payload:
            yield from _walk_components(item, layout_id)
        return
    if not isinstance(payload, dict):
        return
    current_layout_id = payload.get("id") if isinstance(payload.get("id"), str) and isinstance(payload.get("components"), list) else layout_id
    components = payload.get("components")
    if isinstance(components, list):
        for component in components:
            if isinstance(component, dict):
                yield component, current_layout_id
    # These wrappers are the schema's reusable-container fields.  Deliberately do
    # not recurse through arbitrary element ``children``: a child is not a block.
    for key in ("layouts", "raw_layouts", "merged_components"):
        nested = payload.get(key)
        if nested is not None:
            yield from _walk_components(nested, current_layout_id)
