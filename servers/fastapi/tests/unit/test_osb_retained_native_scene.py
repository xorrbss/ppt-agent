import base64
import hashlib
import xml.etree.ElementTree as ET

from services.authored_vnext.generation.retained_reference import (
    OSB_RETAINED_REFERENCE_ID,
    compile_retained_reference_layout,
)
from services.authored_vnext.master.preview import _native_scene_html
from utils.renderer_isolation import MAX_HTML_BYTES, admit_authored_html
from services.authored_vnext.retained_reference_marker import (
    canonical_reference_digest,
    decode_reference_layout_marker,
    encode_reference_layout_marker,
)
from models.authored_vnext.master.contract import SemanticElementContent


_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_EXPECTED_SOURCE = (
    "sha256:747e16a419652b51caaff406be99471f3635c31ec367c47b0f783482057a93dd"
)
_EXPECTED_SLIDES = {
    "cover": 1,
    "section": 3,
    "content": 5,
    "data": 6,
    "timeline": 13,
    "architecture": 15,
    "process": 7,
    "closing": 24,
}


def test_osb_retained_layout_seals_materializable_full_frame_scene() -> None:
    retained = compile_retained_reference_layout(
        OSB_RETAINED_REFERENCE_ID,
        canvas_width=1280,
        canvas_height=720,
    )

    assert retained["sourceDigest"] == _EXPECTED_SOURCE
    assert {item["archetype"] for item in retained["layouts"]} == set(
        _EXPECTED_SLIDES
    )
    for layout in retained["layouts"]:
        scene = layout["nativeScene"]
        assert scene["sourcePackageDigest"] == _EXPECTED_SOURCE
        assert scene["sourceSlideNumber"] == _EXPECTED_SLIDES[layout["archetype"]]
        assert scene["fullSlideRasterAllowed"] is False
        assert scene["digest"] == canonical_reference_digest(
            {key: value for key, value in scene.items() if key != "digest"}
        )
        assert [layer["kind"] for layer in scene["layers"]] == [
            "master",
            "layout",
            "slide",
        ]
        for part in scene["partClosure"]:
            if "partBytesBase64" not in part:
                continue
            value = base64.b64decode(part["partBytesBase64"])
            assert f"sha256:{hashlib.sha256(value).hexdigest()}" == part["digest"]

        slide_layer = scene["layers"][2]
        binding_ids = {
            scene["bindings"]["titleShapeId"],
            *scene["bindings"]["bodyShapeIds"],
        }
        assert binding_ids == set(slide_layer["excludedBindingShapeIds"])
        assert binding_ids.isdisjoint(
            {item["sourceShapeId"] for item in slide_layer["elements"]}
        )

        for layer in scene["layers"]:
            assert layer["sourceElementCount"] >= layer["elementCount"]
            for element in layer["elements"]:
                fragment = element["fragmentXml"]
                encoded = fragment.encode("utf-8")
                expected_digest = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
                assert element["fragmentDigest"] == expected_digest
                assert element["native"]["fragmentXml"] == fragment
                assert element["materialization"] in {
                    "native-ooxml",
                    "element-raster-fallback",
                }
                if layer["kind"] == "slide":
                    root = ET.fromstring(fragment)
                    assert all(
                        not (node.text or "")
                        for node in root.findall(f".//{{{_A}}}t")
                    )
                if element["materialization"] == "element-raster-fallback":
                    assert element["fallback"]["fullSlide"] is False
                    assert element["bounds"] is not None
                    assert element["bounds"]["width"] < scene["sourceCanvas"]["width"]
                    assert element["bounds"]["height"] < scene["sourceCanvas"]["height"]
                for relationship in element["relationships"]:
                    if relationship["external"]:
                        continue
                    value = base64.b64decode(relationship["targetBytesBase64"])
                    assert (
                        f"sha256:{hashlib.sha256(value).hexdigest()}"
                        == relationship["targetDigest"]
                    )

    encoded_marker = encode_reference_layout_marker(retained)
    normalized_chunks = [
        SemanticElementContent(text=encoded_marker[index : index + 9000]).text
        for index in range(0, len(encoded_marker), 9000)
    ]
    assert decode_reference_layout_marker("".join(normalized_chunks)) == retained


def test_osb_process_native_scene_materializes_browser_preview_layer() -> None:
    retained = compile_retained_reference_layout(
        OSB_RETAINED_REFERENCE_ID,
        canvas_width=1280,
        canvas_height=720,
    )
    process = next(
        item for item in retained["layouts"] if item["archetype"] == "process"
    )
    scene = process["nativeScene"]

    html = _native_scene_html(scene)

    assert html.index("native-scene-layer-master") < html.index(
        "native-scene-layer-layout"
    ) < html.index("native-scene-layer-slide")
    assert html.count('class="native-scene-element native-scene-image"') == 2
    assert "data:image/" in html
    assert "background:#0E75BC" in html
    assert 'title="native slide 2:8"' in html
    assert 'title="native slide 2:13"' in html
    assert all(
        not element["native"]["text"]
        for layer in scene["layers"]
        for element in layer["elements"]
    )


def test_osb_cover_native_scene_stays_inside_browser_renderer_admission() -> None:
    retained = compile_retained_reference_layout(
        OSB_RETAINED_REFERENCE_ID,
        canvas_width=1280,
        canvas_height=720,
    )
    cover = next(
        item for item in retained["layouts"] if item["archetype"] == "cover"
    )

    html = _native_scene_html(cover["nativeScene"])

    assert len(html.encode("utf-8")) < MAX_HTML_BYTES
    assert "linear-gradient(60deg,#305286 0%,#305286 33%,rgba(48,82,134,0.5) 100%)" in html
    assert "background:rgba(255,255,255,0.2)" in html
    assert "clip-path:polygon(50% 0,0 100%,100% 100%)" in html
    assert admit_authored_html(html).html
