"""A review that cannot compare a frame names the cause; a template preview falls back to its frame render."""
import asyncio
import uuid
from io import BytesIO
from types import SimpleNamespace as NS

import pytest
from PIL import Image

from services.svg_templates import source_review, storage


def _png(color=(10, 20, 30)):
    buffer = BytesIO()
    Image.new("RGB", (16, 9), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _element(kind, layer):
    rect = NS(x=0, y=0, width=8, height=4, model_dump=lambda mode="json": {"x": 0, "y": 0, "width": 8, "height": 4})
    return NS(element_id=f"{kind}-{layer}", kind=kind, layer=layer, bounds=rect)


def test_frame_with_only_background_shapes_cannot_be_compared():
    frame = NS(elements=[_element("rect", "background")])
    with pytest.raises(ValueError, match="svg_template_review_no_fixed_region"):
        source_review._compare_fixed_regions(_png(), _png(), frame)


def test_background_picture_alone_is_compared():
    frame = NS(elements=[_element("rect", "background"), _element("image", "background")])
    result = source_review._compare_fixed_regions(_png(), _png(), frame)
    assert [r["element_id"] for r in result["regions"]] == ["image-background"]


def test_blocking_cause_is_named_instead_of_the_comparison_failure():
    blocked = NS(reason_code="svg_template_font_unavailable", severity="blocked")
    review = NS(reason_code="source_render_review_required", severity="review-required")
    frame = NS(issues=[review, blocked])
    for code in ("svg_template_review_no_fixed_region", "svg_template_review_render_unavailable",
                 "svg_template_font_unavailable"):
        assert source_review._blocking_cause(frame, code) == "svg_template_font_unavailable"
    # A specific, unrelated failure keeps its own code; so does a frame without a blocked issue.
    assert source_review._blocking_cause(frame, "svg_template_libreoffice_render_failed") == "svg_template_libreoffice_render_failed"
    assert source_review._blocking_cause(NS(issues=[review]), "svg_template_review_no_fixed_region") == "svg_template_review_no_fixed_region"


class _Service:
    def __init__(self, payload, kind="image-derivative", media_type="image/png"):
        self.payload, self.kind, self.media_type = payload, kind, media_type

    async def resolve_ref(self, session, **kwargs):
        return NS(kind=self.kind, media_type=self.media_type)

    async def read_verified_ref_bytes(self, ref, max_bytes):
        return self.payload, None


def _version(frame_ids):
    return NS(review_evidence={"frames": [{"frame_id": fid, "frame_image_ref":
        {"artifact_id": str(uuid.uuid4()), "sha256": "a" * 64}} for fid in frame_ids]})


def test_preview_falls_back_to_the_cover_frame_render(monkeypatch):
    monkeypatch.setattr(storage, "configured_public_artifact_service", lambda: _Service(b"cover-bytes"))
    monkeypatch.setattr("services.svg_templates.catalog.principal", lambda: ("o", "t"))
    material = {"package": {"frames": [{"frame_id": "f6", "role": "body"}, {"frame_id": "f1", "role": "cover"}]}}
    assert asyncio.run(storage.template_frame_preview(None, _version(["f6", "f1"]), material)) == b"cover-bytes"


def test_preview_is_unavailable_without_a_retained_frame_render(monkeypatch):
    monkeypatch.setattr(storage, "configured_public_artifact_service", lambda: _Service(b"x", kind="source-manifest"))
    monkeypatch.setattr("services.svg_templates.catalog.principal", lambda: ("o", "t"))
    material = {"package": {"frames": [{"frame_id": "f1", "role": "cover"}]}}
    assert asyncio.run(storage.template_frame_preview(None, _version(["f1"]), material)) is None
    assert asyncio.run(storage.template_frame_preview(None, NS(review_evidence=None), material)) is None
