from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
import uuid
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from pptx import Presentation
from pptx.chart.data import ChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

from api.v1.ppt.endpoints.source_content import SOURCE_CONTENT_ROUTER
from models.sql.source_content import (
    SourceContentCoverageReceipt,
    SourceContentGenerationBinding,
    SourceContentManifestAsset,
    SourceContentManifestRecord,
)
from services.source_content import manifest_store
from services.source_content.errors import SECURITY_REJECTED, SourceContentError
from services.source_content.pptx import extract_pptx_source_content


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _fixture_pptx(path: Path) -> None:
    image_path = path.with_suffix(".png")
    image_path.write_bytes(_PNG_1X1)
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    text = slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(5), Inches(1))
    paragraph = text.text_frame.paragraphs[0]
    paragraph.add_run().text = "원본 문구 2026"
    table = slide.shapes.add_table(2, 2, Inches(0.5), Inches(2), Inches(5), Inches(2)).table
    table.cell(0, 0).text = "항목"
    table.cell(0, 1).text = "값"
    table.cell(1, 0).text = "매출"
    table.cell(1, 1).text = "100원"
    table.cell(0, 0).merge(table.cell(0, 1))
    slide.shapes.add_picture(str(image_path), Inches(6), Inches(1), Inches(1), Inches(1))
    chart_data = ChartData()
    chart_data.categories = ["A", "B"]
    chart_data.add_series("매출", (10, 20))
    slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(6),
        Inches(3),
        Inches(3),
        Inches(2),
        chart_data,
    )
    deck.save(path)


def _with_external_relationship(source: Path, target: Path) -> None:
    rel_name = "ppt/slides/_rels/slide1.xml.rels"
    with ZipFile(source, "r") as package, ZipFile(target, "w", ZIP_DEFLATED) as output:
        for info in package.infolist():
            payload = package.read(info.filename)
            if info.filename == rel_name:
                payload = payload.replace(
                    b"</Relationships>",
                    b'<Relationship Id="rIdExternal" '
                    b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
                    b'Target="https://example.invalid/" TargetMode="External"/>'
                    b"</Relationships>",
                )
            output.writestr(info, payload)


def _with_standard_presentation_metadata(source: Path, target: Path) -> None:
    rel_name = "ppt/_rels/presentation.xml.rels"
    content_types_name = "[Content_Types].xml"
    with ZipFile(source, "r") as package, ZipFile(target, "w", ZIP_DEFLATED) as output:
        for info in package.infolist():
            payload = package.read(info.filename)
            if info.filename == rel_name:
                payload = payload.replace(
                    b"</Relationships>",
                    b'<Relationship Id="rIdHandoutMetadata" '
                    b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/handoutMaster" '
                    b'Target="handoutMasters/handoutMaster1.xml"/>'
                    b'<Relationship Id="rIdRevisionMetadata" '
                    b'Type="http://schemas.microsoft.com/office/2011/relationships/revisionInfo" '
                    b'Target="revisionInfo.xml"/>'
                    b"</Relationships>",
                )
            elif info.filename == content_types_name:
                payload = payload.replace(
                    b"</Types>",
                    b'<Override PartName="/ppt/handoutMasters/handoutMaster1.xml" '
                    b'ContentType="application/vnd.openxmlformats-officedocument.presentationml.handoutMaster+xml"/>'
                    b'<Override PartName="/ppt/revisionInfo.xml" '
                    b'ContentType="application/vnd.ms-powerpoint.revisioninfo+xml"/>'
                    b"</Types>",
                )
            output.writestr(info, payload)
        output.writestr(
            "ppt/handoutMasters/handoutMaster1.xml",
            b'<p:handoutMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>',
        )
        output.writestr(
            "ppt/revisionInfo.xml",
            b'<p1912:revisionInfo xmlns:p1912="http://schemas.microsoft.com/office/powerpoint/2011/main"/>',
        )


def test_pptx_manifest_is_deterministic_and_external_relationship_fails_closed(
    tmp_path: Path,
) -> None:
    assert {route.path for route in SOURCE_CONTENT_ROUTER.routes} == {
        "/source-content/v1/analyze",
        "/source-content/v1/manifests/{manifest_id}/status",
    }
    assert SourceContentManifestRecord.__tablename__ == "source_content_manifests"
    assert SourceContentGenerationBinding.__tablename__ == "source_content_generation_bindings"
    assert SourceContentCoverageReceipt.__tablename__ == "source_content_coverage_receipts"
    source = tmp_path / "source.pptx"
    _fixture_pptx(source)

    first = extract_pptx_source_content(
        source,
        owner_id="owner-a",
        reference_snapshot_id="11111111-1111-1111-1111-111111111111",
    )
    second = extract_pptx_source_content(
        source,
        owner_id="owner-a",
        reference_snapshot_id="11111111-1111-1111-1111-111111111111",
    )

    assert first.manifest.manifest_digest == second.manifest.manifest_digest
    assert first.manifest.object_inventory.unclassified_object_ids == ()
    assert (
        first.manifest.object_inventory.discovered_object_count
        == first.manifest.object_inventory.classified_object_count
    )
    assert first.manifest.counts.table_cells == 4
    assert first.manifest.counts.image_occurrences == 1
    text_block = next(
        block
        for unit in first.manifest.units
        for block in unit.blocks
        if block.kind == "text"
    )
    assert "".join(text_block.payload["runs"]) == text_block.payload["text"]
    table = next(
        block
        for unit in first.manifest.units
        for block in unit.blocks
        if block.kind == "table"
    )
    assert table.payload["cells"][1] == ["매출", "100원"]
    assert table.payload["mergedCells"] == [
        {"row": 0, "column": 0, "rowSpan": 1, "columnSpan": 2}
    ]
    chart = next(
        block
        for unit in first.manifest.units
        for block in unit.blocks
        if block.kind == "chart"
    )
    assert chart.payload["chartType"] == "column"
    assert chart.payload["categories"] == ["A", "B"]
    assert chart.payload["series"] == [{"name": "매출", "values": [10, 20]}]
    assert first.assets[0].content_digest == second.assets[0].content_digest

    external = tmp_path / "external.pptx"
    _with_external_relationship(source, external)
    with pytest.raises(SourceContentError) as rejected:
        extract_pptx_source_content(
            external,
            owner_id="owner-a",
            reference_snapshot_id="11111111-1111-1111-1111-111111111111",
        )
    assert rejected.value.code == SECURITY_REJECTED
    assert rejected.value.detail["reason"] == "external_relationship"


def test_pptx_manifest_accepts_standard_non_slide_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.pptx"
    _fixture_pptx(source)
    metadata = tmp_path / "metadata.pptx"
    _with_standard_presentation_metadata(source, metadata)

    extracted = extract_pptx_source_content(
        metadata,
        owner_id="owner-a",
        reference_snapshot_id="11111111-1111-1111-1111-111111111111",
    )

    inventory = extracted.manifest.object_inventory
    assert inventory.unclassified_object_ids == ()
    assert inventory.discovered_object_count == inventory.classified_object_count
    classifications = {
        item.object_id: item.classification
        for item in extracted.manifest.inventory_objects
    }
    assert classifications["part:ppt/handoutMasters/handoutMaster1.xml"] == "package_metadata"
    assert classifications["part:ppt/revisionInfo.xml"] == "package_metadata"
    assert (
        classifications[
            "rel:ppt/_rels/presentation.xml.rels:rIdHandoutMetadata"
        ]
        == "supported_relationship"
    )
    assert (
        classifications[
            "rel:ppt/_rels/presentation.xml.rels:rIdRevisionMetadata"
        ]
        == "supported_relationship"
    )


def test_manifest_parent_is_flushed_before_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.pptx"
    _fixture_pptx(source)
    snapshot_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    binding = SimpleNamespace(
        asset_id=uuid.uuid4(),
        sha256="sha256:source",
        version=1,
    )
    snapshot = SimpleNamespace(
        provider_id="uploaded_document",
        asset_bindings=(binding,),
        snapshot_id=snapshot_id,
        source_revision="1",
    )
    asset = SimpleNamespace(
        sha256=binding.sha256,
        version=binding.version,
        original_name="source.pptx",
    )

    async def admit_snapshot(_session, *, snapshot_id):
        assert snapshot_id == snapshot.snapshot_id
        return snapshot

    async def resolve_asset(_session, *, asset_id):
        assert asset_id == binding.asset_id
        return asset, source

    monkeypatch.setattr(manifest_store, "admit_reference_snapshot", admit_snapshot)
    monkeypatch.setattr(
        manifest_store,
        "resolve_workspace_asset_download",
        resolve_asset,
    )

    class RecordingSession:
        def __init__(self) -> None:
            self.events: list[tuple[str, type | None]] = []

        async def scalar(self, _query):
            return None

        def add(self, value) -> None:
            self.events.append(("add", type(value)))

        async def flush(self) -> None:
            self.events.append(("flush", None))

        async def commit(self) -> None:
            self.events.append(("commit", None))

    session = RecordingSession()
    asyncio.run(
        manifest_store.analyze_reference_snapshot(
            session,
            reference_snapshot_id=snapshot_id,
            owner_id=owner_id,
        )
    )

    parent_add = session.events.index(("add", SourceContentManifestRecord))
    parent_flush = session.events.index(("flush", None))
    asset_add = session.events.index(("add", SourceContentManifestAsset))
    commit = session.events.index(("commit", None))
    assert parent_add < parent_flush < asset_add < commit
