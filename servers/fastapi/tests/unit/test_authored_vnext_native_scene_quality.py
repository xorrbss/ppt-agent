from __future__ import annotations

import base64
import copy
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from models.authored_vnext.deck.scene import (
    SceneAssetManifestEntry,
    SceneCapability,
    SceneChartElement,
    SceneDataManifestEntry,
    SceneEditability,
    SceneImageElement,
    SceneImageCropGeometry,
    SceneOwnerDecision,
    SceneRasterIsland,
    SceneRasterZBand,
    SceneShapeElement,
    SceneTableElement,
    SceneTextElement,
    SceneTextStyle,
    SlideHybridExportPlan,
    scene_digest,
    seal_deck_scene,
    seal_slide_scene,
)
from models.authored_vnext.master.contract import Rect
from services.authored_vnext.ooxml.native_scene import (
    NativeSceneError,
    materialize_scene_pptx,
    readback_scene_pptx,
)
from services.authored_vnext.semantic.editable_scene import _reviewed_text_style


_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)


def _capabilities() -> list[SceneCapability]:
    return [
        SceneCapability(
            kind=kind,
            preview="supported" if kind not in {"connector", "group"} else "partial",
            workbench_edit=(
                "supported"
                if kind in {"text", "image", "shape"}
                else "partial"
                if kind in {"connector", "table", "chart"}
                else "readOnly"
            ),
            native_pptx_export=(
                "supported"
                if kind in {"text", "image", "shape", "table", "chart"}
                else "unsupported"
            ),
            reason=(
                "native connector/group export is not implemented"
                if kind in {"connector", "group"}
                else None
            ),
        )
        for kind in ("text", "image", "shape", "connector", "table", "chart", "group")
    ]


def test_browser_text_metrics_are_projected_once_into_native_ooxml() -> None:
    style = _reviewed_text_style(
        SimpleNamespace(
            text="긴 한글 제목과 English",
            style={
                "fontFamily": '"Noto Sans KR", sans-serif',
                "fontSize": "40px",
                "fontWeight": "700",
                "lineHeight": "48px",
                "color": "rgb(16, 42, 86)",
                "textAlign": "left",
                "display": "flex",
                "flexDirection": "column",
                "justifyContent": "center",
                "paddingLeft": "6px",
                "paddingRight": "8px",
                "paddingTop": "2px",
                "paddingBottom": "4px",
            },
        )
    )
    assert style is not None
    assert style.font_size == 30  # 40 CSS px -> 30 PowerPoint pt, exactly once.
    assert style.line_height == 1.2
    assert style.vertical_align == "middle"
    assert (
        style.margin_left_pt,
        style.margin_right_pt,
        style.margin_top_pt,
        style.margin_bottom_pt,
    ) == (4.5, 6, 1.5, 3)
    slide = seal_slide_scene(
        slide_id="slide:measured-title",
        revision=1,
        elements=[
            SceneTextElement(
                kind="text",
                element_id="element:measured-title.title",
                z_order=1,
                bounds=Rect(x=72, y=60, width=816, height=140),
                semantic_role="title",
                editability=SceneEditability(),
                text="긴 한글 제목과 中文日本語\nPowerPoint 품질 검증",
                style=style,
            )
        ],
    )
    scene = seal_deck_scene(
        deck_id="deck:measured-title",
        revision=1,
        composition_digest=f"sha256:{'1' * 64}",
        source_digest=f"sha256:{'2' * 64}",
        slides=[slide],
        capabilities=_capabilities(),
    )
    content, _ = materialize_scene_pptx(
        scene.model_dump(mode="json", by_alias=True),
        [],
        {
            "slideSizePx": {"width": 960, "height": 540},
            "tokens": {"color.background": "#F7F9FC"},
            "furniture": [],
        },
    )

    with ZipFile(BytesIO(content)) as package:
        slide_xml = package.read("ppt/slides/slide1.xml").decode("utf-8")
    assert "<a:noAutofit/>" in slide_xml
    assert "<a:spAutoFit/>" not in slide_xml
    assert slide_xml.count('<a:rPr sz="3000"') == 2
    assert 'lIns="57150"' in slide_xml
    assert 'rIns="76200"' in slide_xml
    assert 'tIns="19050"' in slide_xml
    assert 'bIns="38100"' in slide_xml
    assert 'anchor="ctr"' in slide_xml
    assert '<a:spcPct val="120000"/>' in slide_xml
    assert '<a:spcPts val="0"/>' in slide_xml
    assert slide_xml.count('<a:ea typeface="Noto Sans KR"/>') == 2
    assert slide_xml.count('<a:cs typeface="Noto Sans KR"/>') == 2


def test_scene_materializes_digest_verified_image_table_and_chart_as_native_objects() -> None:
    image_digest = f"sha256:{sha256(_PNG_BYTES).hexdigest()}"
    table_payload = {
        "cells": [["구분", "2025", "2026"], ["매출", "120", "165"]]
    }
    chart_payload = {
        "categories": ["1분기", "2분기", "3분기"],
        "series": [{"name": "매출", "values": [10, 14, 19]}],
    }
    table_digest = scene_digest(table_payload)
    chart_digest = scene_digest(chart_payload)
    slide = seal_slide_scene(
        slide_id="slide:native-quality",
        revision=1,
        elements=[
            SceneShapeElement(
                kind="shape",
                element_id="element:native-quality.accent",
                z_order=1,
                bounds=Rect(x=24, y=24, width=12, height=180),
                semantic_role="metricMarker",
                editability=SceneEditability(geometry=True),
                shape="roundRect",
                fill="#1463FF",
            ),
            SceneTextElement(
                kind="text",
                element_id="element:native-quality.copy",
                z_order=2,
                bounds=Rect(x=48, y=140, width=196, height=64),
                semantic_role="body",
                editability=SceneEditability(text=True, geometry=True),
                text="검증된 데이터",
                style=SceneTextStyle(
                    font_family="Malgun Gothic",
                    font_size=20,
                    font_weight=700,
                    color="#0B1830",
                    text_align="left",
                    vertical_align="middle",
                ),
            ),
            SceneImageElement(
                kind="image",
                element_id="element:native-quality.image",
                z_order=10,
                bounds=Rect(x=48, y=24, width=196, height=100),
                semantic_role="bodyImage",
                editability=SceneEditability(geometry=True, asset=True),
                asset_id=f"asset:{image_digest.removeprefix('sha256:')[:48]}",
                asset_digest=image_digest,
                alt_text="검증용 이미지",
                crop="cover",
            ),
            SceneTableElement(
                kind="table",
                element_id="element:native-quality.table",
                z_order=20,
                bounds=Rect(x=280, y=24, width=640, height=180),
                semantic_role="bodyTable",
                editability=SceneEditability(geometry=True, data=True),
                data_id=f"data:{table_digest.removeprefix('sha256:')[:48]}",
                data_digest=table_digest,
                rows=2,
                columns=3,
            ),
            SceneChartElement(
                kind="chart",
                element_id="element:native-quality.chart",
                z_order=30,
                bounds=Rect(x=24, y=230, width=896, height=280),
                semantic_role="bodyChart",
                editability=SceneEditability(geometry=True, data=True),
                data_id=f"data:{chart_digest.removeprefix('sha256:')[:48]}",
                data_digest=chart_digest,
                chart_type="column",
                accessibility_summary="분기별 매출이 10에서 19로 증가",
            ),
        ],
    )
    asset_id = f"asset:{image_digest.removeprefix('sha256:')[:48]}"
    scene = seal_deck_scene(
        deck_id="deck:native-quality",
        revision=1,
        composition_digest=f"sha256:{'1' * 64}",
        source_digest=f"sha256:{'2' * 64}",
        slides=[slide],
        asset_manifest=[
            SceneAssetManifestEntry(
                asset_id=asset_id,
                asset_digest=image_digest,
                media_type="image/png",
                storage_reference=(
                    "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode("ascii")
                ),
                size_bytes=len(_PNG_BYTES),
            )
        ],
        data_manifest=[
            SceneDataManifestEntry(
                data_id=f"data:{table_digest.removeprefix('sha256:')[:48]}",
                data_digest=table_digest,
                kind="table",
                payload=table_payload,
            ),
            SceneDataManifestEntry(
                data_id=f"data:{chart_digest.removeprefix('sha256:')[:48]}",
                data_digest=chart_digest,
                kind="chart",
                payload=chart_payload,
            ),
        ],
        capabilities=_capabilities(),
    )
    scene_payload = scene.model_dump(mode="json", by_alias=True)
    assets = [
        {
            "assetId": asset_id,
            "mediaType": "image/png",
            "sha256": image_digest.removeprefix("sha256:"),
            "sizeBytes": len(_PNG_BYTES),
            "bytesBase64": base64.b64encode(_PNG_BYTES).decode("ascii"),
        }
    ]
    content, receipt = materialize_scene_pptx(
        scene_payload,
        assets,
        {
            "slideSizePx": {"width": 960, "height": 540},
            "tokens": {"color.background": "#F7F9FC"},
            "furniture": [],
        },
    )

    assert readback_scene_pptx(content) == receipt
    assert receipt["nativeObjectCounts"] == {
        "chart": 1,
        "image": 1,
        "shape": 1,
        "table": 1,
        "text": 1,
    }
    assert receipt["capabilities"] == {
        "supported": ["chart", "image", "shape", "table", "text"],
        "unsupported": ["connector", "group"],
    }
    native_shapes = Presentation(BytesIO(content)).slides[0].shapes
    native_slide = Presentation(BytesIO(content)).slides[0]
    assert str(native_slide.background.fill.fore_color.rgb) == "F7F9FC"
    assert receipt["scene"]["slides"][0]["furniture"][0] == {
        "furnitureId": "company-standard:background",
        "kind": "background",
        "fill": "#F7F9FC",
        "styleDigest": scene_digest({"fill": "#F7F9FC"}),
    }
    assert [shape.shape_type for shape in native_shapes].count(MSO_SHAPE_TYPE.AUTO_SHAPE) == 1
    assert [shape.shape_type for shape in native_shapes].count(MSO_SHAPE_TYPE.TEXT_BOX) == 1
    native_table = next(shape.table for shape in native_shapes if shape.shape_type == MSO_SHAPE_TYPE.TABLE)
    header_run = native_table.cell(0, 0).text_frame.paragraphs[0].runs[0]
    assert str(native_table.cell(0, 0).fill.fore_color.rgb) == "1463FF"
    assert header_run.font.name == "Malgun Gothic"
    assert header_run.font.bold is True
    assert native_table.columns[0].width > native_table.columns[1].width
    native_chart = next(shape.chart for shape in native_shapes if shape.shape_type == MSO_SHAPE_TYPE.CHART)
    assert native_chart.category_axis.tick_labels.font.name == "Malgun Gothic"
    assert str(native_chart.series[0].format.fill.fore_color.rgb) == "1463FF"
    elements = {item["elementId"]: item for item in receipt["scene"]["slides"][0]["elements"]}
    assert elements["element:native-quality.image"]["assetDigest"] == image_digest
    assert elements["element:native-quality.image"]["cropGeometry"] == {
        "left": 0.0,
        "top": 0.2449,
        "right": 0.0,
        "bottom": 0.2449,
    }
    assert elements["element:native-quality.table"]["nativeDataDigest"] == table_digest
    assert elements["element:native-quality.chart"]["nativeDataDigest"] == chart_digest

    corrupt_assets = [{**assets[0], "bytesBase64": base64.b64encode(_PNG_BYTES[:-1] + b"0").decode("ascii")}]
    with pytest.raises(NativeSceneError, match="authored_vnext_scene_asset_digest_mismatch"):
        materialize_scene_pptx(
            scene_payload,
            corrupt_assets,
            {
                "slideSizePx": {"width": 960, "height": 540},
                "tokens": {"color.background": "#F7F9FC"},
                "furniture": [],
            },
        )


def test_editable_hybrid_scene_emits_native_subset_raster_island_and_qid_tags() -> None:
    asset_digest = f"sha256:{sha256(_PNG_BYTES).hexdigest()}"
    asset_id = f"asset:{asset_digest.removeprefix('sha256:')[:48]}"
    table_payload = {
        "cells": [["구분", "Value"], ["수정", "84"]],
        "columnWidthsPt": [160, 320],
        "cellStyles": [
            [
                {
                    "fill": "#1463FF",
                    "borders": {
                        "bottom": {"color": "#0B1830", "widthPt": 1}
                    },
                    "horizontalAlign": "center",
                    "verticalAlign": "middle",
                },
                {
                    "fill": "#1463FF",
                    "horizontalAlign": "center",
                    "verticalAlign": "middle",
                },
            ],
            [{}, {}],
        ],
    }
    chart_payload = {
        "categories": ["1분기", "2분기"],
        "series": [{"name": "매출", "values": [10, 14]}],
    }
    line_chart_payload = {
        "categories": ["Jan", "Feb", "Mar"],
        "series": [{"name": "Revenue", "values": [10, 14.5, 13]}],
    }
    pie_chart_payload = {
        "categories": ["A", "B", "C"],
        "series": [{"name": "Share", "values": [50, 30, 20]}],
    }
    table_digest = scene_digest(table_payload)
    chart_digest = scene_digest(chart_payload)
    line_chart_digest = scene_digest(line_chart_payload)
    pie_chart_digest = scene_digest(pie_chart_payload)
    elements = [
        SceneTextElement(
            kind="text",
            element_id="title.main",
            z_order=1,
            bounds=Rect(x=40, y=30, width=420, height=70),
            semantic_role="title",
            editability=SceneEditability(text=True, geometry=True, style=True),
            text="수정된 제목 · Edited title",
            style=SceneTextStyle(
                font_family="Malgun Gothic",
                font_size=28,
                font_weight=700,
                color="#0B1830",
            ),
        ),
        SceneShapeElement(
            kind="shape",
            element_id="shape.callout",
            z_order=2,
            bounds=Rect(x=680, y=190, width=260, height=290),
            semantic_role="callout",
            editability=SceneEditability(geometry=True, style=True),
            shape="roundRect",
            fill="#DCE8FF",
            stroke="#1463FF",
            stroke_width=1,
        ),
        SceneImageElement(
            kind="image",
            element_id="image.hero",
            z_order=3,
            bounds=Rect(x=500, y=30, width=220, height=155),
            semantic_role="bodyImage",
            editability=SceneEditability(geometry=True, asset=True),
            asset_id=asset_id,
            asset_digest=asset_digest,
            alt_text="교체 이미지",
            crop="cover",
            crop_geometry=SceneImageCropGeometry(
                left=0.1, top=0.05, right=0.1, bottom=0.05
            ),
        ),
        SceneTableElement(
            kind="table",
            element_id="table.metrics",
            z_order=4,
            bounds=Rect(x=40, y=210, width=640, height=150),
            semantic_role="bodyTable",
            editability=SceneEditability(geometry=True, data=True, style=True),
            data_id=f"data:{table_digest.removeprefix('sha256:')[:48]}",
            data_digest=table_digest,
            rows=2,
            columns=2,
        ),
        SceneChartElement(
            kind="chart",
            element_id="chart.native-pie",
            z_order=11,
            bounds=Rect(x=470, y=370, width=210, height=150),
            semantic_role="bodyChart",
            editability=SceneEditability(geometry=True, data=True, style=True),
            data_id=f"data:{pie_chart_digest.removeprefix('sha256:')[:48]}",
            data_digest=pie_chart_digest,
            chart_type="pie",
            accessibility_summary="Editable native pie chart",
        ),
        SceneChartElement(
            kind="chart",
            element_id="chart.native-line",
            z_order=10,
            bounds=Rect(x=700, y=30, width=220, height=160),
            semantic_role="bodyChart",
            editability=SceneEditability(geometry=True, data=True, style=True),
            data_id=f"data:{line_chart_digest.removeprefix('sha256:')[:48]}",
            data_digest=line_chart_digest,
            chart_type="line",
            accessibility_summary="Editable native line chart",
        ),
        SceneChartElement(
            kind="chart",
            element_id="chart.unsupported",
            z_order=20,
            bounds=Rect(x=700, y=210, width=220, height=250),
            semantic_role="bodyChart",
            editability=SceneEditability(),
            data_id=f"data:{chart_digest.removeprefix('sha256:')[:48]}",
            data_digest=chart_digest,
            chart_type="column",
            accessibility_summary="래스터 차트",
        ),
    ]
    hybrid_export = SlideHybridExportPlan(
        owner_decisions=[
            SceneOwnerDecision(
                owner_id="title.main",
                decision="nativeEditable",
                capability_id="editable.text.advanced.v1",
            ),
            SceneOwnerDecision(
                owner_id="shape.callout",
                decision="nativeEditable",
                capability_id="editable.shape.solid-basic.v1",
            ),
            SceneOwnerDecision(
                owner_id="image.hero",
                decision="nativeEditable",
                capability_id="editable.image.rect-crop.v1",
            ),
            SceneOwnerDecision(
                owner_id="table.metrics",
                decision="nativeEditable",
                capability_id="editable.table.advanced.v1",
            ),
            SceneOwnerDecision(
                owner_id="chart.native-line",
                decision="nativeEditable",
                capability_id="editable.chart.line-pie.v1",
            ),
            SceneOwnerDecision(
                owner_id="chart.native-pie",
                decision="nativeEditable",
                capability_id="editable.chart.line-pie.v1",
            ),
            SceneOwnerDecision(
                owner_id="chart.unsupported",
                decision="rasterIsland",
                capability_id="editable.chart.raster.v1",
                reason_code="chart-native-excluded",
            ),
        ],
        raster_islands=[
            SceneRasterIsland(
                island_id="island:chart",
                source_owner_ids=["chart.unsupported"],
                asset_id=asset_id,
                asset_digest=asset_digest,
                bounds=Rect(x=700, y=210, width=220, height=250),
                z_band=SceneRasterZBand(min_z=20, max_z=20),
                raster_plane_digest=asset_digest,
            )
        ],
    )
    slide = seal_slide_scene(
        slide_id="slide:editable-hybrid",
        revision=2,
        elements=elements,
        hybrid_export=hybrid_export,
    )
    scene = seal_deck_scene(
        deck_id="deck:editable-hybrid",
        revision=2,
        composition_digest=f"sha256:{'1' * 64}",
        source_digest=f"sha256:{'2' * 64}",
        hybrid_profile_id="authored-editable-hybrid-ko-en-basic-v1",
        slides=[slide],
        asset_manifest=[
            SceneAssetManifestEntry(
                asset_id=asset_id,
                asset_digest=asset_digest,
                media_type="image/png",
                storage_reference=(
                    "data:image/png;base64,"
                    + base64.b64encode(_PNG_BYTES).decode("ascii")
                ),
                size_bytes=len(_PNG_BYTES),
            )
        ],
        data_manifest=[
            SceneDataManifestEntry(
                data_id=f"data:{table_digest.removeprefix('sha256:')[:48]}",
                data_digest=table_digest,
                kind="table",
                payload=table_payload,
            ),
            SceneDataManifestEntry(
                data_id=f"data:{line_chart_digest.removeprefix('sha256:')[:48]}",
                data_digest=line_chart_digest,
                kind="chart",
                payload=line_chart_payload,
            ),
            SceneDataManifestEntry(
                data_id=f"data:{pie_chart_digest.removeprefix('sha256:')[:48]}",
                data_digest=pie_chart_digest,
                kind="chart",
                payload=pie_chart_payload,
            ),
            SceneDataManifestEntry(
                data_id=f"data:{chart_digest.removeprefix('sha256:')[:48]}",
                data_digest=chart_digest,
                kind="chart",
                payload=chart_payload,
            ),
        ],
        capabilities=_capabilities(),
    )
    scene_payload = scene.model_dump(mode="json", by_alias=True)
    advanced_table_payload = copy.deepcopy(table_payload)
    advanced_table_payload["cells"][0][1] = ""
    advanced_table_payload["rowHeightsPt"] = [45, 105]
    advanced_table_payload["mergedCells"] = [
        {"row": 0, "column": 0, "rowSpan": 1, "columnSpan": 2}
    ]
    advanced_table_digest = scene_digest(advanced_table_payload)
    advanced_line_payload = copy.deepcopy(line_chart_payload)
    advanced_line_payload["style"] = {
        "chartStyle": 4,
        "seriesColors": ["#1463FF"],
        "legend": False,
    }
    advanced_line_digest = scene_digest(advanced_line_payload)
    advanced_pie_payload = copy.deepcopy(pie_chart_payload)
    advanced_pie_payload["style"] = {
        "chartStyle": 6,
        "seriesColors": ["#F59E0B"],
        "legend": True,
    }
    advanced_pie_digest = scene_digest(advanced_pie_payload)
    table_manifest = next(
        item for item in scene_payload["dataManifest"] if item["kind"] == "table"
    )
    table_manifest["payload"] = advanced_table_payload
    table_manifest["dataDigest"] = advanced_table_digest
    line_manifest = next(
        item
        for item in scene_payload["dataManifest"]
        if item["dataId"] == f"data:{line_chart_digest.removeprefix('sha256:')[:48]}"
    )
    line_manifest["payload"] = advanced_line_payload
    line_manifest["dataDigest"] = advanced_line_digest
    pie_manifest = next(
        item
        for item in scene_payload["dataManifest"]
        if item["dataId"] == f"data:{pie_chart_digest.removeprefix('sha256:')[:48]}"
    )
    pie_manifest["payload"] = advanced_pie_payload
    pie_manifest["dataDigest"] = advanced_pie_digest
    table_element = next(
        item
        for item in scene_payload["slides"][0]["elements"]
        if item["elementId"] == "table.metrics"
    )
    table_element["dataDigest"] = advanced_table_digest
    line_element = next(
        item
        for item in scene_payload["slides"][0]["elements"]
        if item["elementId"] == "chart.native-line"
    )
    line_element["dataDigest"] = advanced_line_digest
    pie_element = next(
        item
        for item in scene_payload["slides"][0]["elements"]
        if item["elementId"] == "chart.native-pie"
    )
    pie_element["dataDigest"] = advanced_pie_digest
    title_payload = next(
        item
        for item in scene_payload["slides"][0]["elements"]
        if item["elementId"] == "title.main"
    )
    title_payload["content"] = {
        "runs": [
            {"text": "긴 한글 English ", "fontFamily": "Malgun Gothic", "bold": True},
            {"text": "", "fontFamily": "Malgun Gothic", "underline": True},
            {"text": "identity", "fontFamily": "Malgun Gothic", "italic": True},
        ]
    }
    title_payload["style"]["autoFit"] = "shrink"
    slide_payload = scene_payload["slides"][0]
    slide_payload["slideDigest"] = scene_digest(
        {key: value for key, value in slide_payload.items() if key != "slideDigest"}
    )
    scene_payload["sceneDigest"] = scene_digest(
        {key: value for key, value in scene_payload.items() if key != "sceneDigest"}
    )
    assets = [
        {
            "assetId": asset_id,
            "mediaType": "image/png",
            "sha256": asset_digest.removeprefix("sha256:"),
            "sizeBytes": len(_PNG_BYTES),
            "bytesBase64": base64.b64encode(_PNG_BYTES).decode("ascii"),
        }
    ]
    content, receipt = materialize_scene_pptx(
        scene_payload,
        assets,
        {
            "slideSizePx": {"width": 960, "height": 540},
            "tokens": {"color.background": "#F7F9FC"},
            "furniture": [],
        },
    )

    assert readback_scene_pptx(content) == receipt
    assert receipt["nativeObjectCounts"] == {
        "chart": 2,
        "image": 1,
        "shape": 1,
        "table": 1,
        "text": 1,
    }
    assert receipt["hybridOwnership"] == {
        "profileId": "authored-editable-hybrid-ko-en-basic-v1",
        "nativeOwnerCount": 6,
        "rasterIslandCount": 1,
        "rasterOwnerCount": 1,
        "nativeRasterDuplicateOwners": 0,
        "missingOrGhostQids": 0,
    }
    assert receipt["scene"]["slides"][0]["rasterIslands"][0][
        "sourceOwnerIds"
    ] == ["chart.unsupported"]
    title_receipt = next(
        item
        for item in receipt["scene"]["slides"][0]["elements"]
        if item["elementId"] == "title.main"
    )
    assert title_receipt["autoFit"] == "shrink"
    assert title_receipt["textRunMap"][1]["utf16Length"] == 0
    stacking = receipt["scene"]["slides"][0]["stackingOrder"]
    assert stacking.index({"layer": "scene", "id": "shape.callout"}) < stacking.index(
        {"layer": "rasterIsland", "id": "island:chart"}
    )
    with ZipFile(BytesIO(content)) as package:
        chart_parts = [
            name
            for name in package.namelist()
            if name.startswith("ppt/charts/chart") and name.endswith(".xml")
        ]
        assert len(chart_parts) == 2
        chart_xml = "\n".join(
            package.read(name).decode("utf-8") for name in chart_parts
        )
        assert "<c:lineChart>" in chart_xml
        assert "<c:pieChart>" in chart_xml
        assert chart_xml.count("<c:strCache>") >= 2
        assert chart_xml.count("<c:numCache>") >= 2
        assert "Revenue" in chart_xml
        assert "Share" in chart_xml
        tag_parts = sorted(
            name for name in package.namelist() if name.startswith("ppt/tags/tag")
        )
        assert len(tag_parts) == 6
        tag_xml = package.read(tag_parts[0]).decode("utf-8")
        assert "PRESENTON_QID_V2" in tag_xml
        assert "Q2_" in tag_xml
        slide_xml = package.read("ppt/slides/slide1.xml").decode("utf-8")
        assert "Presenton hybrid text title.main" in slide_xml
        assert "Presenton raster island island:chart" in slide_xml
        assert "<a:normAutofit" in slide_xml
        assert "<a:t/>" in slide_xml
        assert 'typeface="Malgun Gothic"' in slide_xml

    native_table = next(
        shape.table
        for shape in Presentation(BytesIO(content)).slides[0].shapes
        if shape.shape_type == MSO_SHAPE_TYPE.TABLE
    )
    assert native_table.cell(0, 0).is_merge_origin
    assert native_table.cell(0, 1).is_spanned
    assert [round(row.height / 12700) for row in native_table.rows] == [45, 105]

    invalid = copy.deepcopy(scene_payload)
    invalid["slides"][0]["hybridExport"]["rasterIslands"][0][
        "sourceOwnerIds"
    ].append("title.main")
    invalid_slide = invalid["slides"][0]
    invalid_slide["slideDigest"] = scene_digest(
        {key: value for key, value in invalid_slide.items() if key != "slideDigest"}
    )
    invalid["sceneDigest"] = scene_digest(
        {key: value for key, value in invalid.items() if key != "sceneDigest"}
    )
    with pytest.raises(
        NativeSceneError, match="authored_vnext_hybrid_raster_island_invalid"
    ):
        materialize_scene_pptx(
            invalid,
            assets,
            {
                "slideSizePx": {"width": 960, "height": 540},
                "tokens": {"color.background": "#F7F9FC"},
                "furniture": [],
            },
        )


@pytest.mark.parametrize("kind", ["connector", "group"])
def test_connector_and_group_native_capabilities_remain_explicitly_unsupported(
    kind: str,
) -> None:
    with pytest.raises(ValueError, match="must remain explicitly unsupported"):
        SceneCapability(
            kind=kind,
            preview="partial",
            workbench_edit="readOnly",
            native_pptx_export="supported",
            reason="must not be promoted without an implemented native renderer",
        )
