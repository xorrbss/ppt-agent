import asyncio
import io
import json
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from api.v1.ppt.endpoints import outlines as outlines_endpoint
from api.v1.ppt.endpoints import presentation_generate
from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.presentation import PresentationModel
from services.documents_loader import DocumentsLoader
from tests.conftest import FakeAsyncSession
from utils.document_context import (
    HARD_MAX_DOCUMENT_CONTEXT_CHARS,
    TRUNCATION_MARKER,
    DocumentContextLimitError,
    build_document_context,
    get_document_context_limits,
)


def _run(coro):
    return asyncio.run(coro)


def test_build_document_context_accepts_exact_limits_without_truncating():
    documents = ["가" * 3, "나" * 3]

    context = build_document_context(
        documents,
        ["C:/private/first.md", "C:/private/second.txt"],
        total_limit=8,
        per_document_limit=3,
    )

    assert context == f"{documents[0]}\n\n{documents[1]}"


def test_default_limits_accept_large_single_document(
    monkeypatch,
):
    monkeypatch.delenv("PRESENTON_MAX_DOCUMENT_CONTEXT_CHARS", raising=False)
    monkeypatch.delenv("PRESENTON_MAX_DOCUMENT_CHARS", raising=False)
    document = "x" * 117_486

    assert get_document_context_limits() == (500_000, 500_000)
    assert build_document_context([document], ["uploaded.txt"]) == document


def test_text_loader_accepts_large_document(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("PRESENTON_MAX_DOCUMENT_CONTEXT_CHARS", raising=False)
    monkeypatch.delenv("PRESENTON_MAX_DOCUMENT_CHARS", raising=False)
    document = "x" * 117_486
    document_path = tmp_path / "uploaded.txt"
    document_path.write_text(document, encoding="utf-8")

    loader = DocumentsLoader(file_paths=[str(document_path)])

    assert _run(loader.load_text(str(document_path))) == document


def test_build_document_context_rejects_one_character_over_per_document_limit():
    secret_body = "TOP-SECRET-CONTENT"
    full_path = "C:/private/customer/reference.md"

    with pytest.raises(DocumentContextLimitError) as exc:
        build_document_context(
            [secret_body],
            [full_path],
            total_limit=100,
            per_document_limit=len(secret_body) - 1,
        )

    detail = exc.value.detail
    assert exc.value.status_code == 413
    assert detail["code"] == "document_context_too_large"
    assert detail["scope"] == "document"
    assert detail["filename"] == "reference.md"
    assert secret_body not in json.dumps(detail)
    assert full_path not in json.dumps(detail)


def test_build_document_context_counts_separators_in_total_limit():
    with pytest.raises(DocumentContextLimitError) as exc:
        build_document_context(
            ["abc", "def"],
            ["first.md", "second.md"],
            total_limit=7,
            per_document_limit=3,
        )

    assert exc.value.detail["scope"] == "total"
    assert exc.value.detail["actual_chars"] == 8
    assert exc.value.detail["limit_chars"] == 7


def test_build_document_context_compacts_small_overflow_for_generation():
    document = "A" * 150_000 + "Z" * 62_282

    context = build_document_context(
        [document],
        ["uploaded.txt"],
        total_limit=200_000,
        overflow_strategy="truncate",
    )

    assert len(context) == 200_000
    assert context.startswith("A")
    assert context.endswith("Z")
    assert TRUNCATION_MARKER in context


def test_build_document_context_compacts_all_sources_proportionally():
    documents = ["first:" + "A" * 120_000, "second:" + "B" * 92_282]

    context = build_document_context(
        documents,
        ["first.txt", "second.txt"],
        total_limit=200_000,
        overflow_strategy="truncate",
    )

    assert len(context) == 200_000
    assert context.startswith("first:")
    assert "\n\nsecond:" in context
    assert "A\n\nsecond:" in context
    assert context.endswith("B")


def test_configured_limits_are_clamped_to_hard_max(monkeypatch):
    monkeypatch.setenv("PRESENTON_MAX_DOCUMENT_CONTEXT_CHARS", "999999")
    monkeypatch.setenv("PRESENTON_MAX_DOCUMENT_CHARS", "999999")

    assert get_document_context_limits() == (
        HARD_MAX_DOCUMENT_CONTEXT_CHARS,
        HARD_MAX_DOCUMENT_CONTEXT_CHARS,
    )


def test_load_text_reads_only_a_bounded_prefix_for_oversized_input():
    class TrackingBytesIO(io.BytesIO):
        def __init__(self, value: bytes):
            super().__init__(value)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return super().read(size)

    stream = TrackingBytesIO(b"A" * 100_000)
    loader = DocumentsLoader(file_paths=[])

    with patch(
        "services.documents_loader.get_document_context_limits",
        return_value=(80_000, 10),
    ), patch(
        "services.documents_loader.open",
        create=True,
        return_value=stream,
    ), pytest.raises(DocumentContextLimitError) as exc:
        _run(loader.load_text("C:/private/customer/reference.md"))

    assert stream.read_sizes == [45]
    assert exc.value.status_code == 413
    assert exc.value.detail["filename"] == "reference.md"
    assert "C:/private/customer" not in json.dumps(exc.value.detail)


def test_text_loader_allows_generation_to_compact_below_hard_ceiling(tmp_path):
    document_path = tmp_path / "uploaded.txt"
    document_path.write_text("x" * 212_282, encoding="utf-8")
    loader = DocumentsLoader(
        file_paths=[str(document_path)],
        allow_context_truncation=True,
    )

    document = _run(loader.load_text(str(document_path)))

    assert len(document) == 212_282


def test_outline_stream_emits_safe_error_and_skips_llm_for_oversized_document(
    tmp_path,
):
    presentation_id = uuid.uuid4()
    presentation = PresentationModel(
        id=presentation_id,
        lifecycle_status="staging",
        content="Create a deck.",
        n_slides=1,
        language="English",
        file_paths=[str(tmp_path / "oversized.md")],
        tone="professional",
        verbosity="standard",
        instructions=None,
    )
    session = FakeAsyncSession(
        get_results={presentation_id: presentation},
        # The outline writer admits only a staged deck whose generate operation
        # is still awaiting activation; this is that deck.
        scalar_result=uuid.uuid4(),
    )
    request = types.SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    limit_error = DocumentContextLimitError(
        actual_chars=50_001,
        limit_chars=50_000,
        scope="document",
        filename="oversized.md",
    )

    async def consume_stream():
        response = await outlines_endpoint.stream_outlines(
            presentation_id,
            request,
            session,
        )
        return [chunk async for chunk in response.body_iterator]

    loader = MagicMock()
    loader.load_documents = AsyncMock(side_effect=limit_error)
    loader.documents = []

    with patch.object(
        outlines_endpoint.TEMP_FILE_SERVICE,
        "create_temp_dir",
        return_value=str(tmp_path),
    ), patch.object(
        outlines_endpoint,
        "DocumentsLoader",
        return_value=loader,
    ), patch.object(
        outlines_endpoint,
        "generate_content_brief",
        new=AsyncMock(),
    ) as mock_brief, patch.object(
        outlines_endpoint,
        "generate_ppt_outline",
    ) as mock_outline:
        chunks = _run(consume_stream())

    combined = "".join(
        chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for chunk in chunks
    )
    assert "document_context_too_large" in combined
    assert "oversized.md" in combined
    assert "C:/private" not in combined
    mock_brief.assert_not_awaited()
    mock_outline.assert_not_called()


def test_direct_generation_loader_keeps_document_within_new_default_limit():
    loader = MagicMock()
    loader.load_documents = AsyncMock()
    loader.documents = ["A" * 200_000 + "Z" * 12_282]

    with patch.object(
        presentation_generate,
        "DocumentsLoader",
        return_value=loader,
    ) as loader_class:
        context = _run(
            presentation_generate._load_document_context(
                ["C:/private/customer/oversized.md"],
                "English",
            )
        )

    assert len(context) == 212_282
    assert context.startswith("A")
    assert context.endswith("Z")
    assert TRUNCATION_MARKER not in context
    loader_class.assert_called_once_with(
        file_paths=["C:/private/customer/oversized.md"],
        presentation_language="English",
        allow_context_truncation=True,
    )


def test_async_queue_rejects_oversized_context_before_creating_job():
    request = GeneratePresentationRequest(
        content="Create a deck.",
        files=["C:/server-temp/oversized.md"],
    )
    session = FakeAsyncSession()
    background_tasks = BackgroundTasks()
    limit_error = DocumentContextLimitError(
        actual_chars=50_001,
        limit_chars=50_000,
        scope="document",
        filename="oversized.md",
    )

    with patch.object(
        presentation_generate,
        "check_if_api_request_is_valid",
        new=AsyncMock(return_value=(uuid.uuid4(),)),
    ), patch.object(
        presentation_generate,
        "_load_document_context",
        new=AsyncMock(side_effect=limit_error),
    ) as mock_preflight, pytest.raises(DocumentContextLimitError) as exc:
        _run(
            presentation_generate.queue_presentation_generation(
                request,
                background_tasks,
                session,
                # The nested-generation dispatch used by workspace
                # orchestration; it reaches the same document preflight
                # without needing a second bootstrap operation.
                force_legacy_dispatch=True,
            )
        )

    assert exc.value.status_code == 413
    mock_preflight.assert_awaited_once_with(request.files, request.language)
    assert session.added == []
    assert session.commit_count == 0
    assert background_tasks.tasks == []
