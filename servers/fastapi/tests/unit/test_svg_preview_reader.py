from __future__ import annotations

import asyncio
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
import uuid

import pytest
from PIL import Image

from services.artifact_store import ArtifactBindingError
from services.svg_asset_manifest import PlacementManifest, SourceManifest
from services.svg_asset_resolver import materialize_svg, resolve_slide_svg_preview
from services.svg_contract import source_digest


@pytest.fixture
def preview_case():
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format="PNG")
    payload = output.getvalue()
    derivative = dict(ref=str(uuid.uuid4()), sha256=sha256(payload).hexdigest(),
                      width=2, height=2, mediaType="image/png", byteSize=len(payload),
                      recipeDigest="sha256:" + "a" * 64)
    slots = []
    for name in ("one", "two"):
        slots.append(dict(slotId=f"source-slot-{name}", sourceAssetId=str(uuid.uuid4()),
            sourceSha256=derivative["sha256"], sourceWidth=2, sourceHeight=2,
            semanticDescription="original chart", occurrenceId=str(uuid.uuid4()),
            sourceLocator=dict(sourceFormat="image", coordinateSpace="intrinsic"),
            extractionKind="direct_image", sourceAppearanceSha256=derivative["sha256"],
            preservationClass="supporting", selectionReason="selected original",
            policy=dict(kind="mandatory", required=True, allowOmit=False, allowExcludeOnFailure=False),
            modelDerivative=derivative))
    source = SourceManifest.seal(dict(presentationId=str(uuid.uuid4()), sourceRevision=0,
        slots=slots, offeredPages=[dict(pageIndex=i, slotIds=[s["slotId"]]) for i, s in enumerate(slots)]))
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
           '<text x="40" y="60" font-family="Arial" font-size="28" fill="#111111">Chart</text>'
           '<image x="40" y="100" width="800" height="500" href="source-slot-one" '
           'preserveAspectRatio="xMidYMid meet"/></svg>')
    placement = PlacementManifest.seal(dict(canonicalSvgDigest=source_digest(svg),
        sourceManifestDigest=source.digest, slideRevision=0, purpose="screen",
        rendererVersion="svg-chromium/v1", placements=[dict(slotId=slots[0]["slotId"],
            occurrenceId=slots[0]["occurrenceId"], x=40, y=100, width=800, height=500,
            derivative=derivative)]))
    return SimpleNamespace(svg=svg, source=source, placement=placement,
                           derivative=derivative, payload=payload)


def test_page_scope_does_not_require_another_pages_image(preview_case):
    c = preview_case
    result = materialize_svg(c.svg, manifest=c.source,
        derivatives={"source-slot-one": c.derivative}, payloads={"source-slot-one": c.payload},
        width=1280, height=720, placement_manifest=c.placement, page_index=0)
    assert "data:image/png;base64," in result
    assert "data:" not in c.svg


def test_wrong_page_placement_is_rejected(preview_case):
    c = preview_case
    with pytest.raises(ValueError, match="placement_slot_not_offered"):
        materialize_svg(c.svg, manifest=c.source, derivatives={}, payloads={},
            width=1280, height=720, placement_manifest=c.placement, page_index=1)


def test_tampered_canonical_svg_is_rejected(preview_case):
    c = preview_case
    with pytest.raises(ValueError, match="placement_source_mismatch"):
        materialize_svg(c.svg.replace('x="40"', 'x="41"'), manifest=c.source,
            derivatives={}, payloads={}, width=1280, height=720,
            placement_manifest=c.placement, page_index=0)


def test_tampered_derivative_bytes_are_rejected(preview_case):
    c = preview_case
    with pytest.raises(ValueError, match="derivative_digest_mismatch"):
        materialize_svg(c.svg, manifest=c.source, derivatives={"source-slot-one": c.derivative},
            payloads={"source-slot-one": c.payload[:-1] + b"x"}, width=1280, height=720,
            placement_manifest=c.placement, page_index=0)


class ManifestStore:
    def __init__(self, c, owner):
        self.owner, self.reads = owner, 0
        self.ref = uuid.uuid4()
        self.payload = c.source.model_dump_json(by_alias=True).encode()

    async def resolve_ref(self, session, **binding):
        if binding["owner_id"] != self.owner or binding["tenant_id"] != "tenant-one":
            raise ArtifactBindingError("owner_or_tenant_mismatch")
        return SimpleNamespace(kind="source-manifest", media_type="application/json")

    async def read_ref_bytes(self, reference):
        self.reads += 1
        return self.payload


def _slide(c, store):
    return SimpleNamespace(svg_content=c.svg, index=0, content={"svgAssetManifest": {
        "schemaVersion": "svg-slide-assets/v1", "canonicalVersion": "svg-static/v2",
        "slideRevision": 0, "sourceManifest": {"ref": str(store.ref),
            "sha256": sha256(store.payload).hexdigest()}, "screenPlacement": None}})


@pytest.mark.parametrize("wrong_tenant", [False, True])
def test_cross_owner_or_tenant_cannot_read_manifest(preview_case, wrong_tenant):
    owner = uuid.uuid4()
    store = ManifestStore(preview_case, owner)
    with pytest.raises(ArtifactBindingError, match="owner_or_tenant_mismatch"):
        asyncio.run(resolve_slide_svg_preview(_slide(preview_case, store), session=object(),
            owner_id=owner if wrong_tenant else uuid.uuid4(),
            tenant_id="tenant-two" if wrong_tenant else "tenant-one",
            width=1280, height=720, service=store))
    assert store.reads == 0


def test_manifest_bytes_digest_is_verified(preview_case):
    owner = uuid.uuid4()
    store = ManifestStore(preview_case, owner)
    slide = _slide(preview_case, store)
    store.payload += b" "
    with pytest.raises(ArtifactBindingError, match="manifest_bytes_mismatch"):
        asyncio.run(resolve_slide_svg_preview(slide, session=object(), owner_id=owner,
            tenant_id="tenant-one", width=1280, height=720, service=store))


def test_public_slide_projection_preserves_internal_manifest():
    from models.presentation_with_slides import PresentationWithSlides
    from models.sql.slide import SlideModel
    content = {"title": "Original title", "svgAssetManifest": {"sourceManifest": {"ref": "private"}}}
    slide = SlideModel(
        presentation=uuid.uuid4(), layout_group="svg", layout="svg:content",
        index=0, content=content, svg_content=(
            '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
            'viewBox="0 0 1280 720"><text x="10" y="30" font-family="Arial" '
            'font-size="20" fill="#111111">Title</text></svg>'
        ), source_format="svg", properties={},
    )
    response = PresentationWithSlides.model_construct(slides=[slide])
    public = response.model_dump(mode="json", include={"slides"})
    assert public["slides"][0]["content"] == {"title": "Original title"}
    assert slide.content == content
    assert "svgAssetManifest" in slide.model_dump()["content"]
