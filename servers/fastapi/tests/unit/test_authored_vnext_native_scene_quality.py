from __future__ import annotations

import base64
from hashlib import sha256
from io import BytesIO

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
    SceneShapeElement,
    SceneTableElement,
    SceneTextElement,
    SceneTextStyle,
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
