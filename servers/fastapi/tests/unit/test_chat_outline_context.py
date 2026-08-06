import uuid
from typing import Any

import pytest

from utils.owner_context import (
    reset_current_owner_id,
    set_current_owner_id,
)
from constants.presentation import MAX_NUMBER_OF_SLIDES
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from services.chat.outline_context import OutlineTemplateContext


class _Session:
    def __init__(self, presentation: PresentationModel, scalar_results: list[list[Any]] | None = None):
        self.presentation = presentation
        self.scalar_results = list(scalar_results or [])
        self.added: list[Any] = []
        # Slide reads reconcile against the session's pending unit of work, so
        # the double has to model it rather than only record calls.
        self.new: list[Any] = []
        self.deleted: list[Any] = []
        self.commit_count = 0
        self.rollback_count = 0

    async def get(self, model, key):
        if model is PresentationModel and key == self.presentation.id:
            return self.presentation
        return None

    async def scalars(self, *_args, **_kwargs):
        return self.scalar_results.pop(0) if self.scalar_results else []

    async def execute(self, *_args, **_kwargs):
        return None

    def add(self, value):
        self.added.append(value)
        if value not in self.new:
            self.new.append(value)

    async def delete(self, value):
        self.deleted.append(value)

    async def commit(self):
        self.commit_count += 1
        self.new.clear()
        self.deleted.clear()

    async def rollback(self):
        self.rollback_count += 1
        self.new.clear()
        self.deleted.clear()

    async def refresh(self, _value):
        return None


class _Memory:
    def __init__(self):
        self.saved: list[dict[str, Any]] = []

    async def get(self, _key):
        return None

    async def search(self, query, limit=5):
        return [{"query": query, "limit": limit}]

    async def get_available_layouts(self):
        return [{"id": "title", "name": "Title"}]

    async def get_content_schema_from_layout_id(self, layout_id):
        return {"id": layout_id}

    async def save_slide(self, **kwargs):
        self.saved.append(kwargs)
        return {"saved": True, **kwargs}


def _presentation(*, outlines=None, file_paths=None):
    return PresentationModel(
        id=uuid.uuid4(),
        content="context",
        n_slides=0,
        language="en",
        title="Existing title",
        outlines=outlines,
        file_paths=file_paths,
        layout={"name": "deck"},
    )


def _context(presentation, scalar_results=None):
    session = _Session(presentation, scalar_results)
    memory = _Memory()
    return OutlineTemplateContext(
        sql_session=session,
        presentation_id=presentation.id,
        memory=memory,
    ), session, memory


@pytest.mark.anyio
async def test_outline_mutations_are_bounded_normalized_and_persisted(monkeypatch):
    presentation = _presentation(outlines={"slides": [{"content": "# One"}, {"content": "Two"}]})
    context, session, _ = _context(presentation)

    async def no_memory_index(*_args, **_kwargs):
        raise RuntimeError("index offline")

    monkeypatch.setattr(
        "services.chat.outline_context.MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines",
        no_memory_index,
    )

    added = await context.add_outline(content="  Three  ", index=1)
    assert [item["content"] for item in added["slides"]] == ["# One", "Three", "Two"]
    assert presentation.outlines == {
        "slides": [{"content": "# One"}, {"content": "Three"}, {"content": "Two"}]
    }
    assert presentation.title == "One"

    moved = await context.move_outline(from_index=2, to_index=0)
    assert [item["content"] for item in moved["slides"]] == ["Two", "# One", "Three"]
    deleted = await context.delete_outline(index=1)
    assert [item["content"] for item in deleted["slides"]] == ["Two", "Three"]
    assert session.commit_count == 3


@pytest.mark.anyio
async def test_outline_commit_failure_restores_presentation_state():
    presentation = _presentation(outlines={"slides": [{"content": "One"}]})
    context, session, _ = _context(presentation)

    async def fail_commit():
        session.commit_count += 1
        raise RuntimeError("database unavailable")

    session.commit = fail_commit
    with pytest.raises(RuntimeError, match="database unavailable"):
        await context.update_outline(index=0, content="Changed")

    assert presentation.outlines == {"slides": [{"content": "One"}]}
    assert presentation.n_slides == 0
    assert session.rollback_count == 1


@pytest.mark.anyio
async def test_layout_slide_uses_existing_save_path_and_max_slide_guard():
    presentation = _presentation()
    context, _, memory = _context(presentation, scalar_results=[[]])

    result = await context.add_new_slide_layout(
        content={"title": "Hello"}, layout_id="title", index=None
    )
    assert result["saved"] is True
    assert memory.saved == [
        {
            "content": {"title": "Hello"},
            "layout_id": "title",
            "index": 0,
            "replace_old_slide_at_index": False,
        }
    ]

    context, _, memory = _context(
        presentation,
        scalar_results=[
            [
                SlideModel(
                    presentation=presentation.id,
                    layout_group="deck",
                    layout="title",
                    index=position,
                    content={},
                    properties=None,
                )
                for position in range(MAX_NUMBER_OF_SLIDES)
            ]
        ],
    )
    blocked = await context.add_new_slide_layout(
        content={}, layout_id="title", index=None
    )
    assert blocked["error"] == "max_slides_reached"
    assert memory.saved == []


@pytest.mark.anyio
async def test_cross_owner_presentation_and_slides_are_not_exposed():
    owner = uuid.uuid4()
    other_owner = uuid.uuid4()
    presentation = _presentation()
    presentation.owner_id = other_owner
    context, _, _ = _context(presentation, scalar_results=[[object()]])
    token = set_current_owner_id(owner)
    try:
        assert await context.get_outline_draft() == {
            "found": False,
            "error": "presentation_not_found",
        }
        assert await context.add_new_slide_layout(
            content={"title": "Blocked"}, layout_id="title"
        ) == {"found": False, "error": "presentation_not_found"}
    finally:
        reset_current_owner_id(token)


@pytest.mark.anyio
async def test_cross_owner_slide_does_not_affect_insert_index():
    owner = uuid.uuid4()
    presentation = _presentation()
    presentation.owner_id = owner
    hidden_slide = SlideModel(
        owner_id=uuid.uuid4(),
        presentation=presentation.id,
        layout_group="deck",
        layout="title",
        index=7,
        content={},
        properties=None,
    )
    context, _, memory = _context(presentation, scalar_results=[[hidden_slide]])
    token = set_current_owner_id(owner)
    try:
        result = await context.add_new_slide_layout(
            content={"title": "Visible"}, layout_id="title"
        )
    finally:
        reset_current_owner_id(token)

    assert result["index"] == 0
    assert memory.saved[0]["index"] == 0


@pytest.mark.anyio
async def test_blank_slide_preserves_native_ui_html_exclusivity():
    presentation = _presentation()
    context, session, _ = _context(presentation, scalar_results=[[]])

    result = await context.add_new_slide(index=0)
    created = next(value for value in session.added if value.__class__.__name__ == "SlideModel")
    assert result["created"] is True
    assert created.ui is None
    assert created.html_content is None
    assert created.layout == "__blank_slide__"
    assert presentation.n_slides == 1


@pytest.mark.anyio
async def test_available_blocks_only_uses_associated_template_and_filters_type():
    presentation = _presentation()
    template = TemplateV2(
        id="owned-template",
        name="Owned",
        components={
            "components": [
                {
                    "id": "title-block",
                    "name": "Title block",
                    "elements": [{"type": "text", "children": [{"type": "image"}]}],
                }
            ]
        },
    )
    context, _, _ = _context(presentation, scalar_results=[[template]])

    result = await context.get_available_blocks(element_type="image", include_full_content=True)
    assert result["truncated"] is False
    assert result["blocks"] == [
        {
            "id": "title-block",
            "name": "Title block",
            "description": None,
            "template_id": "owned-template",
            "layout_id": None,
            "element_types": ["image", "text"],
            "component": template.components["components"][0],
        }
    ]


def test_catalog_default_outline_scope_requires_release_ready(monkeypatch):
    owner_id = uuid.uuid4()
    catalog_default = TemplateV2(
        id="catalog-default",
        owner_id=None,
        name="Catalog default",
        assets={"release": "ready"},
        is_default=False,
    )
    stale_database_default = TemplateV2(
        id="stale-default",
        owner_id=None,
        name="Stale database default",
        is_default=True,
    )
    owned_custom = TemplateV2(
        id="owned-custom",
        owner_id=owner_id,
        name="Owned custom",
    )
    monkeypatch.setattr(
        "services.chat.outline_context.get_template_v2_catalog_entry",
        lambda template_id: object() if template_id == "catalog-default" else None,
    )
    monkeypatch.setattr(
        "services.chat.outline_context.is_template_v2_default_release",
        lambda template_id, assets: (
            template_id == "catalog-default" and assets == {"release": "ready"}
        ),
    )

    token = set_current_owner_id(owner_id)
    try:
        assert OutlineTemplateContext._is_template_scoped(catalog_default)
        catalog_default.assets = {"release": "blocked"}
        assert not OutlineTemplateContext._is_template_scoped(catalog_default)
        assert not OutlineTemplateContext._is_template_scoped(
            stale_database_default
        )
        assert OutlineTemplateContext._is_template_scoped(owned_custom)
    finally:
        reset_current_owner_id(token)


@pytest.mark.anyio
async def test_source_document_rejects_unowned_path_before_loader(monkeypatch):
    presentation = _presentation(file_paths=["/not-owner/secret.txt"])
    context, _, _ = _context(presentation)
    called = False

    def reject(_path, *, must_exist):
        assert must_exist is True
        raise ValueError("outside owner directory")

    class ShouldNotLoad:
        def __init__(self, *_args, **_kwargs):
            nonlocal called
            called = True

    monkeypatch.setattr("services.chat.outline_context.TEMP_FILE_SERVICE.resolve_temp_path", reject)
    monkeypatch.setattr("services.chat.outline_context.DocumentsLoader", ShouldNotLoad)

    result = await context.read_source_documents()
    assert result["documents"] == []
    assert result["errors"] == [{"name": "secret.txt", "error": "unavailable"}]
    assert called is False


@pytest.mark.anyio
async def test_source_document_content_is_budgeted_after_safe_resolution(monkeypatch):
    presentation = _presentation(file_paths=["/owner/source.txt"])
    context, _, _ = _context(presentation)

    class Loader:
        def __init__(self, paths, language):
            assert paths == ["/owner/source.txt"]
            assert language == "en"
            self.documents = []

        async def load_documents(self, **kwargs):
            assert kwargs == {"load_text": True, "load_images": False}
            self.documents = ["x" * 1_500]

    monkeypatch.setattr(
        "services.chat.outline_context.TEMP_FILE_SERVICE.resolve_temp_path",
        lambda path, *, must_exist: path,
    )
    monkeypatch.setattr("services.chat.outline_context.DocumentsLoader", Loader)

    result = await context.read_source_documents(max_chars=1_000)
    assert result["documents"][0]["name"] == "source.txt"
    assert len(result["documents"][0]["content"]) == 1_000
    assert result["documents"][0]["truncated"] is True
