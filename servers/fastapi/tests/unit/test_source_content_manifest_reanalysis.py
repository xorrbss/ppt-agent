"""Regression: re-analysing an already-analysed snapshot must not 409.

Commit ``53881cb00`` enriched the pptx extractor with run-level typography
(``pptx_typography.py`` / ``pptx_text_body.py``, consumed by ``pptx.py``).
That enrichment changes the manifest's content -- and therefore its
``manifest_digest`` -- for a snapshot that was already analysed under the
older, simpler extractor output. Because ``manifest_id`` was derived only
from ``owner_id`` / ``reference_snapshot_id`` / ``source_digest`` (see
``extract_pptx_source_content`` in ``pptx.py``), re-running
``analyze_reference_snapshot`` on the *same* snapshot found the previously
stored row under the *same* manifest_id but with a *different* digest, and
``manifest_store._manifest_from_record``'s digest comparison rejected it as
a 409 -- even though this was a legitimate re-analysis, not tampering.

The fix folds ``MANIFEST_ID_EXTRACTOR_REVISION`` (``pptx_typography.py``)
into the ``manifest_id`` seed, so an extractor revision bump seals a new
manifest identity for the same snapshot instead of colliding with the
digest already stored under the old one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import uuid

from pptx import Presentation
from pptx.util import Inches, Pt
import pytest

from models.sql.source_content import (
    SourceContentManifestAsset,
    SourceContentManifestRecord,
)
from services.source_content import manifest_store
from services.source_content import pptx as pptx_module
from services.source_content import pptx_typography
from services.source_content.errors import SourceContentError
from services.source_content.pptx import extract_pptx_source_content


def _fixture_pptx(path: Path) -> None:
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(5), Inches(1))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "Reanalysis fixture"
    run.font.size = Pt(32)
    run.font.bold = True
    deck.save(path)


def _neutered_run_style(_chain: Any) -> dict[str, Any]:
    """Stand-in for the pre-enrichment extractor, which captured no typography."""

    return {"sizePt": None, "bold": False, "italic": False, "color": None}


def test_extractor_revision_bump_changes_manifest_id_for_the_same_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revision bump must change ``manifest_id``, not just ``manifest_digest``."""

    source = tmp_path / "deck.pptx"
    _fixture_pptx(source)

    monkeypatch.setattr(pptx_module, "MANIFEST_ID_EXTRACTOR_REVISION", "rev-a")
    before = extract_pptx_source_content(
        source, owner_id="owner-a", reference_snapshot_id="snap-a"
    )

    monkeypatch.setattr(pptx_module, "MANIFEST_ID_EXTRACTOR_REVISION", "rev-b")
    after = extract_pptx_source_content(
        source, owner_id="owner-a", reference_snapshot_id="snap-a"
    )

    assert before.manifest.manifest_id != after.manifest.manifest_id
    assert before.manifest.manifest_digest != after.manifest.manifest_digest


@dataclass(frozen=True)
class _ManifestLookupKey:
    owner_scope: str
    manifest_id: str


class _StatefulManifestSession:
    """An ``AsyncSession`` stand-in that actually persists inserted rows.

    ``manifest_store._manifest_query`` is monkeypatched (in the test below)
    to return ``_ManifestLookupKey`` instead of a SQLAlchemy ``Select``, so
    this fake can resolve "does a row already exist for this manifest_id"
    without a real database -- the same idea the existing
    ``test_manifest_parent_is_flushed_before_assets`` regression uses, just
    keyed so it actually distinguishes one manifest_id from another.
    """

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], SourceContentManifestRecord] = {}
        self.assets: list[SourceContentManifestAsset] = []

    async def scalar(self, key: _ManifestLookupKey) -> SourceContentManifestRecord | None:
        return self.rows.get((key.owner_scope, key.manifest_id))

    def add(self, value: Any) -> None:
        if isinstance(value, SourceContentManifestRecord):
            self.rows[(value.owner_scope, value.manifest_id)] = value
        elif isinstance(value, SourceContentManifestAsset):
            self.assets.append(value)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None


def test_reanalysing_an_already_analysed_snapshot_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-running analysis after the extractor gains typography must not 409."""

    source = tmp_path / "deck.pptx"
    _fixture_pptx(source)
    snapshot_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    binding = SimpleNamespace(asset_id=uuid.uuid4(), sha256="sha256:source", version=1)
    snapshot = SimpleNamespace(
        provider_id="uploaded_document",
        asset_bindings=(binding,),
        snapshot_id=snapshot_id,
        source_revision="1",
    )
    asset = SimpleNamespace(sha256=binding.sha256, version=binding.version, original_name="deck.pptx")

    async def admit_snapshot(_session: Any, *, snapshot_id: uuid.UUID) -> SimpleNamespace:
        assert snapshot_id == snapshot.snapshot_id
        return snapshot

    async def resolve_asset(_session: Any, *, asset_id: uuid.UUID) -> tuple[Any, Path]:
        assert asset_id == binding.asset_id
        return asset, source

    def fake_manifest_query(*, owner_id: Any, manifest_id: str) -> _ManifestLookupKey:
        return _ManifestLookupKey(
            owner_scope=manifest_store._scope(owner_id), manifest_id=manifest_id
        )

    monkeypatch.setattr(manifest_store, "admit_reference_snapshot", admit_snapshot)
    monkeypatch.setattr(manifest_store, "resolve_workspace_asset_download", resolve_asset)
    monkeypatch.setattr(manifest_store, "_manifest_query", fake_manifest_query)

    session = _StatefulManifestSession()
    real_revision = pptx_module.MANIFEST_ID_EXTRACTOR_REVISION
    real_resolve_run_style = pptx_typography.resolve_run_style

    # Pass 1: the snapshot as it was analysed before commit 53881cb00 shipped
    # run-level typography capture -- older revision, no resolved run styles.
    monkeypatch.setattr(pptx_module, "MANIFEST_ID_EXTRACTOR_REVISION", "pre-typography")
    monkeypatch.setattr(pptx_typography, "resolve_run_style", _neutered_run_style)
    first = asyncio.run(
        manifest_store.analyze_reference_snapshot(
            session, reference_snapshot_id=snapshot_id, owner_id=owner_id
        )
    )
    assert len(session.rows) == 1

    # Pass 2: today's enriched extractor re-analyses the *same* snapshot --
    # restore the real revision and typography resolution (no code reverted,
    # this is the actual re-analysis flow being exercised).
    monkeypatch.setattr(pptx_module, "MANIFEST_ID_EXTRACTOR_REVISION", real_revision)
    monkeypatch.setattr(pptx_typography, "resolve_run_style", real_resolve_run_style)

    second = asyncio.run(
        manifest_store.analyze_reference_snapshot(
            session, reference_snapshot_id=snapshot_id, owner_id=owner_id
        )
    )

    assert first.manifest_pin.manifest_id != second.manifest_pin.manifest_id
    assert len(session.rows) == 2
    # The enriched pass actually captured typography this time.
    assert second.summary.text_blocks == first.summary.text_blocks


def test_conflicting_digest_under_the_same_manifest_id_still_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sanity check: the digest-mismatch guard itself must still work.

    If a manifest ever changes content *without* a revision bump (a
    determinism bug, not a legitimate extractor upgrade), the same
    manifest_id colliding with a different digest must still be rejected.
    This guards against the fix above being loosened into "never conflict".
    """

    source = tmp_path / "deck.pptx"
    _fixture_pptx(source)
    snapshot_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    binding = SimpleNamespace(asset_id=uuid.uuid4(), sha256="sha256:source", version=1)
    snapshot = SimpleNamespace(
        provider_id="uploaded_document",
        asset_bindings=(binding,),
        snapshot_id=snapshot_id,
        source_revision="1",
    )
    asset = SimpleNamespace(sha256=binding.sha256, version=binding.version, original_name="deck.pptx")

    async def admit_snapshot(_session: Any, *, snapshot_id: uuid.UUID) -> SimpleNamespace:
        return snapshot

    async def resolve_asset(_session: Any, *, asset_id: uuid.UUID) -> tuple[Any, Path]:
        return asset, source

    def fake_manifest_query(*, owner_id: Any, manifest_id: str) -> _ManifestLookupKey:
        return _ManifestLookupKey(
            owner_scope=manifest_store._scope(owner_id), manifest_id=manifest_id
        )

    monkeypatch.setattr(manifest_store, "admit_reference_snapshot", admit_snapshot)
    monkeypatch.setattr(manifest_store, "resolve_workspace_asset_download", resolve_asset)
    monkeypatch.setattr(manifest_store, "_manifest_query", fake_manifest_query)

    session = _StatefulManifestSession()

    asyncio.run(
        manifest_store.analyze_reference_snapshot(
            session, reference_snapshot_id=snapshot_id, owner_id=owner_id
        )
    )
    assert len(session.rows) == 1

    # Same revision (unchanged), but the resolved content differs -- this is
    # a determinism bug, not an extractor upgrade, and must still 409.
    monkeypatch.setattr(pptx_typography, "resolve_run_style", _neutered_run_style)
    with pytest.raises(SourceContentError) as excinfo:
        asyncio.run(
            manifest_store.analyze_reference_snapshot(
                session, reference_snapshot_id=snapshot_id, owner_id=owner_id
            )
        )
    assert excinfo.value.status_code == 409
