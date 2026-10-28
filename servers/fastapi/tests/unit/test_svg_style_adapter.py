from types import SimpleNamespace
from dataclasses import replace

import pytest

from utils.authored_styles import load_authored_styles, resolve_authored_style
from services.generation_core.drivers.svg.svg_style_adapter import adapt_svg_style


def _request(primary_color=None, fonts=None, wordmark=None):
    return SimpleNamespace(primary_color=primary_color, fonts=fonts, wordmark=wordmark)


def test_no_request_falls_back_to_the_preset_only_as_before():
    style = resolve_authored_style("default")
    result = adapt_svg_style(style, width=1280, height=720)
    assert result["tokens"]["primary"] == (style.primary_color or style.preview_accent)
    assert result["tokens"]["wordmark"] == ""


def test_request_brand_overrides_win_over_the_preset():
    style = resolve_authored_style("default")
    result = adapt_svg_style(
        style, width=1280, height=720,
        request=_request(primary_color="#FF00AA", fonts="Pretendard", wordmark="Acme Corp"),
    )
    assert result["tokens"]["primary"] == "#FF00AA"
    assert result["tokens"]["heading_font"] == "Pretendard"
    assert result["tokens"]["body_font"] == "Pretendard"
    assert result["tokens"]["wordmark"] == "Acme Corp"


def test_partial_request_overrides_leave_the_preset_field_alone():
    style = resolve_authored_style("default")
    result = adapt_svg_style(style, width=1280, height=720, request=_request(wordmark="Only Wordmark"))
    assert result["tokens"]["primary"] == (style.primary_color or style.preview_accent)
    assert result["tokens"]["heading_font"] == (style.heading_font or "Noto Sans KR")
    assert result["tokens"]["wordmark"] == "Only Wordmark"


def test_master_rules_and_pin_are_carried_into_the_snapshot():
    style = resolve_authored_style("default")
    rules = {"style_id": "master:acme-1", "tokens": {"accent": "#111111"}}
    pin = {"masterId": "master:acme-1", "masterRevision": 1}
    result = adapt_svg_style(
        style, width=1280, height=720,
        request=_request(), master_rules=rules, master_pin=pin,
    )
    assert result["master_template"] == rules
    assert result["master_pin"] == pin


def test_master_rules_govern_tokens_primary_and_fonts_when_not_overridden():
    style = resolve_authored_style("default")
    rules = {
        "style_id": "master:acme-1",
        "tokens": {"accent": "#7A1FA2", "background": "#111111"},
        "fonts": {"heading": {"family": "Malgun Gothic"}, "body": {"family": "Malgun Gothic"}},
    }
    result = adapt_svg_style(
        style, width=1280, height=720, request=_request(), master_rules=rules,
    )
    assert result["tokens"]["primary"] == "#7A1FA2"
    assert result["tokens"]["background"] == "#111111"
    assert result["tokens"]["heading_font"] == "Malgun Gothic"
    assert result["tokens"]["body_font"] == "Malgun Gothic"


def test_explicit_request_override_still_wins_over_a_master():
    style = resolve_authored_style("default")
    rules = {"style_id": "master:acme-1", "tokens": {"accent": "#7A1FA2"},
             "fonts": {"heading": {"family": "Malgun Gothic"}, "body": {"family": "Malgun Gothic"}}}
    result = adapt_svg_style(
        style, width=1280, height=720,
        request=_request(primary_color="#00FF00", fonts="Pretendard"), master_rules=rules,
    )
    assert result["tokens"]["primary"] == "#00FF00"
    assert result["tokens"]["heading_font"] == "Pretendard"


def test_no_master_rules_means_no_master_keys_in_the_snapshot():
    style = resolve_authored_style("default")
    result = adapt_svg_style(style, width=1280, height=720)
    assert "master_template" not in result
    assert "master_pin" not in result


@pytest.mark.parametrize("width,height", [(0, 720), (1280, 0), (5000, 720)])
def test_canvas_bounds_are_still_enforced(width, height):
    style = resolve_authored_style("default")
    with pytest.raises(ValueError, match="svg_style_canvas_unsupported"):
        adapt_svg_style(style, width=width, height=height)


def test_every_validated_style_projects_identity_without_fixed_topology():
    for style in load_authored_styles():
        result = adapt_svg_style(style, width=840, height=1188, source_template=True)
        assert result["composition_mode"] == "free-svg"
        assert result["reference_semantics"] == "design-identity"
        assert "LAYOUT SYSTEM" not in result["common_guidance"]
        assert "SLIDE ARCHETYPES" not in result["common_guidance"]
        assert "layout_hints" not in result["overrides"]
        assert "layouts" not in result["source_template"]
        assert result["source_template"]["content_contract"] == "verbatim-source-content"


def test_exact_label_and_html_instructions_do_not_block_svg_design_reference():
    style = replace(resolve_authored_style("default"), id="private-reference",
                    reference_mode="exact_reconstruction", brief="MOOD\nCalm and precise\nLAYOUT SYSTEM\nUse CSS flexbox")
    result = adapt_svg_style(style)
    assert result["reference_semantics"] == "design-identity"
    assert result["diagnostics"]


def test_master_reference_does_not_require_source_title_slots():
    from models.authored_vnext.master.contract import DeckMasterContract
    from services.generation_core.style.master_projection import project_master_reference
    from tests.support.authored_vnext_master_fixture import master_contract_payload

    payload = master_contract_payload()
    payload["layouts"]["standard"]["slots"] = []
    contract = DeckMasterContract.model_validate(payload)
    result = project_master_reference(contract, 840, 1188)
    assert result["tokens"]["accent"] == contract.tokens.colors.accent
    assert "layouts" not in result
    assert result["provenance"]["master_digest"] == contract.digest


def test_trusted_pack_pin_projects_across_canvases_and_rejects_digest_change(tmp_path, monkeypatch):
    import uuid
    from services.authored_vnext.design_pack import ingest_design_pack
    from services.authored_vnext import generation_route
    from services.generation_core.style.design_pack_projection import DesignPackProjectionError, resolve_design_pack_reference
    from tests.unit import test_source_native_design_pack as fixture

    monkeypatch.setattr(fixture, "_PRESENTATION", fixture._PRESENTATION.replace('cy="6858000"', 'cy="12192000"'))
    source, storage = tmp_path / "reference.pptx", tmp_path / "packs"
    fixture._synthetic_pptx(source)
    manifest = ingest_design_pack(source, pack_id="reference-blue", package_version="1.0.0", storage_root=storage)
    owner = uuid.uuid4()
    seen = []
    def owner_storage(owner_id):
        seen.append(owner_id)
        return storage
    monkeypatch.setattr(generation_route, "design_pack_storage_root", owner_storage)
    pin = {"designPackId": "reference-blue", "version": 1, "digest": manifest.package_digest}
    profile = resolve_design_pack_reference(owner_id=owner, pin=pin, width=840, height=1188)
    assert seen == [owner]
    assert profile["composition_mode"] == "free-svg"
    assert profile["provenance"]["package_digest"] == manifest.package_digest
    assert "layouts" not in profile and "slots" not in profile
    assert profile["references"] == {}  # Structural outlines are evidence, not art direction.
    with pytest.raises(DesignPackProjectionError, match="pin_digest_mismatch"):
        resolve_design_pack_reference(owner_id=owner, pin={**pin, "digest": "sha256:" + "0" * 64}, width=840, height=1188)


def test_master_logo_is_owner_verified_reanchored_and_stamped_once(tmp_path, monkeypatch):
    import io
    import uuid
    import xml.etree.ElementTree as ET
    from PIL import Image
    from models.authored_vnext.master.contract import DeckMasterContract
    from services.authored_vnext.master.assets import OwnerMasterAssetStore
    from services.generation_core.style.master_projection import enrich_master_reference, project_master_reference
    from services.generation_core.drivers.svg.native_declarations import native_binding_digest
    from services.svg_contract import finalize_svg, validate_svg
    from tests.support.authored_vnext_master_fixture import master_contract_payload
    import utils.get_env

    owner = uuid.uuid4()
    monkeypatch.setattr(utils.get_env, "get_app_data_directory_env", lambda: str(tmp_path))
    logo = io.BytesIO()
    Image.new("RGB", (80, 40), "blue").save(logo, format="PNG")
    store = OwnerMasterAssetStore(tmp_path / "authored_vnext" / "master-assets" / "users" / str(owner))
    asset = store.publish_png(logo.getvalue())
    payload = master_contract_payload()
    payload["brandAssets"]["logo"] = {"assetId": asset["assetId"], "digest": asset["digest"], "altText": "Logo"}
    payload["furniture"]["logo"]["assetId"] = asset["assetId"]
    contract = DeckMasterContract.model_validate(payload)
    rules = enrich_master_reference(project_master_reference(contract, 840, 1188), contract,
                                    owner_id=owner, width=840, height=1188)
    snapshot = {"master_template": rules}
    raw = '<svg xmlns="http://www.w3.org/2000/svg" width="840" height="1188" viewBox="0 0 840 1188"><text x="100" y="100" font-family="Noto Sans KR" font-size="24" fill="#000000">Content</text></svg>'
    result = finalize_svg(raw, index=1, n=2, width=840, height=1188, style_snapshot=snapshot, source_manifest={})
    assert finalize_svg(result, index=1, n=2, width=840, height=1188, style_snapshot=snapshot, source_manifest={}) == result
    root = ET.fromstring(result)
    nodes = {node.get("id"): node for node in root.iter() if node.get("id")}
    assert nodes["svg-system-master-logo"].get("preserveAspectRatio") == "xMidYMid meet"
    assert nodes["svg-system-master-page-number"].text == "02"
    assert float(nodes["svg-system-master-footer"].get("y")) > 1100
    assert native_binding_digest(raw) == native_binding_digest(result)
    with pytest.raises(ValueError, match="unknown_slot"):
        validate_svg(result, width=840, height=1188, source_manifest={})


@pytest.mark.parametrize("format", ["pptx", "pdf", "png", "jpeg", "webp"])
def test_uploaded_artifact_design_reference_uses_signals_and_bounded_examples(tmp_path, format, monkeypatch):
    import uuid
    from PIL import Image
    from services.svg_uploaded_design_reference import project_uploaded_design_reference, validate_uploaded_design_reference
    from services.references import ReferenceProviderError
    from services.svg_contract import validate_raster_data_url
    from tests.unit.test_artifact_style_builder import _pptx, _text_pdf
    from utils.hashing import sha256_hex
    from services.document_conversion_service import DocumentConversionService
    monkeypatch.setattr(DocumentConversionService, "is_soffice_available", lambda self: False)

    path = tmp_path / ("reference." + format)
    if format == "pptx":
        _pptx(path, tmp_path / "embedded.png")
        media = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    elif format == "pdf":
        _text_pdf(path)
        media = "application/pdf"
    else:
        Image.new("RGB", (240, 120), "#2563EB").save(path, format=format.upper())
        media = "image/" + format
    profile = project_uploaded_design_reference(path, expected_sha256=sha256_hex(path.read_bytes()), media_type=media,
        snapshot_id=uuid.uuid4(), snapshot_content_sha256="a" * 64, width=840, height=1188)
    assert profile["composition_mode"] == "free-svg"
    assert profile["provenance"]["source_format"] == (format if format in {"pdf", "pptx"} else "image")
    assert profile["identity"]["colors"]
    adapted = adapt_svg_style(resolve_authored_style("default"), width=840, height=1188,
        request=_request(), design_pack_rules=profile)
    assert adapted["tokens"]["primary"] == profile["tokens"]["accent"]
    assert adapted["tokens"]["heading_font"] == profile["fonts"]["heading"]["family"]
    assert adapted["tokens"]["body_font"] == profile["fonts"]["body"]["family"]
    assert adapted["tokens"]["palette"] == profile["tokens"]["palette"]
    assert "layouts" not in profile and "slots" not in profile
    assert str(tmp_path) not in str(profile)
    assert len(profile.get("references", {}).get("content", [])) <= 2
    for example in profile.get("references", {}).get("content", []):
        assert validate_raster_data_url(example["data_url"]) <= 512 * 1024
    original_id = uuid.UUID(profile["provenance"]["snapshot_id"])
    validate_uploaded_design_reference(profile, snapshot_id=original_id, width=840, height=1188)
    with pytest.raises(ReferenceProviderError, match="binding_mismatch"):
        validate_uploaded_design_reference(profile, snapshot_id=uuid.uuid4(), width=840, height=1188)
    with pytest.raises(ReferenceProviderError, match="digest_mismatch"):
        validate_uploaded_design_reference({**profile, "digest": "sha256:" + "0" * 64}, snapshot_id=original_id, width=840, height=1188)


def test_uploaded_design_reference_admits_real_owner_snapshot_and_rejects_changed_bytes(tmp_path, monkeypatch):
    import asyncio
    import io
    import uuid
    from PIL import Image
    from services.assets import AssetGateway
    from services.references import ReferenceProviderError, create_reference_snapshot
    from services.svg_uploaded_design_reference import prepare_svg_uploaded_design_reference
    from tests.unit.test_asset_gateway_and_references import _database, _owner, _seed_asset, _seed_owner

    async def scenario():
        engine, maker = await _database(tmp_path / "svg-reference.db")
        owner, other = uuid.uuid4(), uuid.uuid4()
        root = tmp_path / "assets"
        gateway = AssetGateway(storage_root=root)
        try:
            await _seed_owner(maker, owner)
            await _seed_owner(maker, other)
            raw = io.BytesIO()
            Image.new("RGB", (240, 120), "#2563EB").save(raw, format="PNG")
            asset = await _seed_asset(maker, root=root, owner_id=owner, content=raw.getvalue(), media_type="image/png", extension=".png")
            with _owner(owner):
                async with maker() as session:
                    snapshot = await create_reference_snapshot(session, provider_id="uploaded_document", request={"asset_ids": [str(asset.id)]}, asset_gateway=gateway)
                    request = SimpleNamespace(svg_design_reference_snapshot_id=snapshot.snapshot_id, _svg_uploaded_design_reference=None,
                                              aspect_ratio="9:16", page_size=None, orientation=None, _document_canvas=None)
                    profile = await prepare_svg_uploaded_design_reference(request, session, asset_gateway=gateway)
                    assert profile["provenance"]["snapshot_id"] == str(snapshot.snapshot_id)
                    assert request._svg_uploaded_design_reference is profile
                    from utils.hashing import canonical_json_digest
                    from copy import deepcopy
                    import services.svg_uploaded_design_reference as uploaded_module
                    def no_reproject(*_args, **_kwargs):
                        raise AssertionError("cached identity must not rerun the projector")
                    monkeypatch.setattr(uploaded_module, "project_uploaded_design_reference", no_reproject)
                    cached = await prepare_svg_uploaded_design_reference(request, session, asset_gateway=gateway)
                    assert cached is profile
                    request._svg_uploaded_design_reference = None
                    resumed = await prepare_svg_uploaded_design_reference(
                        request, session, asset_gateway=gateway, frozen_profile=profile)
                    assert resumed is profile and request._svg_uploaded_design_reference is profile
                    projected = adapt_svg_style(resolve_authored_style("default"),
                        **profile["canvas"], request=_request(), design_pack_rules=profile)
                    assert "references" not in projected["design_pack_template"]
                    assert projected["references"] == profile["references"]
                    request._svg_style_snapshot = deepcopy(projected)
                    before_projection = deepcopy(request._svg_style_snapshot)
                    replayed = await prepare_svg_uploaded_design_reference(request, session,
                        asset_gateway=gateway, frozen_profile=request._svg_style_snapshot["design_pack_template"])
                    assert replayed == profile and replayed["digest"] == profile["digest"]
                    assert request._svg_style_snapshot == before_projection
                    for alteration in ("changed", "missing", "extra", "root_missing"):
                        corrupted = deepcopy(projected)
                        if alteration == "changed":
                            corrupted["references"]["content"][0]["data_url"] += "A"
                        elif alteration == "missing":
                            corrupted["references"]["content"] = []
                        elif alteration == "extra":
                            corrupted["references"]["content"].append(deepcopy(corrupted["references"]["content"][0]))
                        else:
                            corrupted.pop("references")
                        request._svg_style_snapshot = corrupted
                        with pytest.raises(ReferenceProviderError, match="digest_mismatch"):
                            await prepare_svg_uploaded_design_reference(request, session,
                                asset_gateway=gateway, frozen_profile=corrupted["design_pack_template"])
                    request._svg_style_snapshot = None
                    request._svg_uploaded_design_reference = profile
                    import os
                    import json
                    from pathlib import Path
                    replay_evidence = os.environ.get("HEADER_UPLOADED_REFERENCE_REPLAY_EVIDENCE")
                    if replay_evidence:
                        evidence_path = Path(replay_evidence)
                        evidence_bytes = await asyncio.to_thread(evidence_path.read_bytes)
                        evidence = json.loads(evidence_bytes.decode("utf-8-sig"))
                        frozen_evidence = evidence["request_payload"]["_internal_svg_style_snapshot"]
                        inner_evidence = frozen_evidence["design_pack_template"]
                        restored = uploaded_module._restore_uploaded_design_reference(inner_evidence, frozen_evidence)
                        uploaded_module.validate_uploaded_design_reference(restored,
                            snapshot_id=uuid.UUID(restored["provenance"]["snapshot_id"]), **restored["canvas"])
                        assert restored["digest"] == inner_evidence["digest"]
                        assert await asyncio.to_thread(evidence_path.read_bytes) == evidence_bytes
                    from datetime import timedelta
                    from models.sql.reference_snapshot import ReferenceSnapshot
                    from utils.datetime_utils import get_current_utc_datetime
                    stored_snapshot = await session.get(ReferenceSnapshot, snapshot.snapshot_id)
                    previous_expiry = stored_snapshot.expires_at
                    stored_snapshot.expires_at = get_current_utc_datetime() - timedelta(seconds=1)
                    await session.flush()
                    with pytest.raises(ReferenceProviderError, match="snapshot_expired"):
                        await prepare_svg_uploaded_design_reference(request, session, asset_gateway=gateway)
                    stored_snapshot.expires_at = previous_expiry
                    await session.flush()
                    for field in ("snapshot_content_sha256", "source_digest"):
                        tampered = deepcopy(profile)
                        tampered["provenance"][field] = "sha256:" + "0" * 64
                        tampered["digest"] = canonical_json_digest({key: value for key, value in tampered.items() if key != "digest"})
                        request._svg_uploaded_design_reference = tampered
                        with pytest.raises(ReferenceProviderError, match="binding_mismatch"):
                            await prepare_svg_uploaded_design_reference(request, session, asset_gateway=gateway)
                    request._svg_uploaded_design_reference = profile
            with _owner(other):
                async with maker() as session:
                    with pytest.raises(ReferenceProviderError, match="not_found"):
                        await prepare_svg_uploaded_design_reference(request, session, asset_gateway=gateway, frozen_profile=profile)
            with _owner(owner):
                async with maker() as session:
                    _binding, path = await gateway.resolve_for_processing(session, asset_id=asset.id, kind="conversation_attachment")
                    path.write_bytes(raw.getvalue() + b"changed")
                    with pytest.raises(ReferenceProviderError):
                        await prepare_svg_uploaded_design_reference(request, session, asset_gateway=gateway, frozen_profile=profile)
        finally:
            await engine.dispose()
    asyncio.run(scenario())


def test_uploaded_identity_cache_reuses_extraction_across_canvases_but_rechecks_source(tmp_path, monkeypatch):
    from collections import OrderedDict
    import uuid
    from PIL import Image
    import services.svg_uploaded_design_reference as uploaded
    from services.references import ReferenceProviderError
    from utils.hashing import sha256_hex

    monkeypatch.setattr(uploaded, "_EXTRACTION_CACHE", OrderedDict())
    monkeypatch.setattr(uploaded, "_EXTRACTION_CACHE_LIMIT", 2)
    original = uploaded._example
    calls = []
    def observed_example(raw, **kwargs):
        calls.append(sha256_hex(raw))
        return original(raw, **kwargs)
    monkeypatch.setattr(uploaded, "_example", observed_example)
    source = tmp_path / "identity.png"
    def project(color, width=840, height=1188):
        Image.new("RGB", (240, 120), color).save(source)
        return uploaded.project_uploaded_design_reference(source, expected_sha256=sha256_hex(source.read_bytes()),
            media_type="image/png", snapshot_id=uuid.uuid4(), snapshot_content_sha256="a" * 64,
            width=width, height=height)
    first = project("#234567")
    digest = sha256_hex(source.read_bytes())
    second = project("#234567", 1280, 720)
    assert len(calls) == 1 and first["identity"] == second["identity"]
    assert first["digest"] != second["digest"] and first["canvas"] != second["canvas"]
    project("#ABCDEF")
    project("#C86432")
    assert len(uploaded._EXTRACTION_CACHE) == 2
    project("#234567")
    assert len(calls) == 4
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ReferenceProviderError, match="binding_stale"):
        uploaded.project_uploaded_design_reference(source, expected_sha256=digest,
            media_type="image/png", snapshot_id=uuid.uuid4(), snapshot_content_sha256="a" * 64, width=840, height=1188)


def test_uploaded_pdf_examples_include_middle_and_ending_pages(tmp_path):
    from services.svg_uploaded_design_reference import _pdf_examples
    from tests.unit.test_artifact_style_builder import _write_pdf
    path = tmp_path / "five-pages.pdf"
    pages = [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 320 180] /Resources << >> >>"] * 5
    _write_pdf(path, [b"<< /Type /Catalog /Pages 2 0 R >>",
                     b"<< /Type /Pages /Kids [3 0 R 4 0 R 5 0 R 6 0 R 7 0 R] /Count 5 >>", *pages])
    examples = _pdf_examples(path.read_bytes())
    assert [example["source_page_index"] for example in examples] == [0, 1, 2, 4]
    assert all(example["data_url"].startswith("data:image/") for example in examples)


def test_uploaded_semantic_palette_respects_observed_background_and_text():
    from services.svg_reference_identity import color_identity, typography_identity
    palette, roles = color_identity([
        {"value": "#111827", "roles": ["background"], "count": 2},
        {"value": "#FFFFFF", "roles": ["text"], "count": 40},
        {"value": "#FFD900", "roles": ["fill"], "count": 10},
    ], [])
    assert roles["background"] == "#111827" and roles["foreground"] == "#FFFFFF"
    assert roles["accent"] == "#FFD900" and "#FFD900" in palette
    type_roles = typography_identity(["Unsupported Uploaded Font"], [
        {"role": "title", "size_pt": 36, "count": 10}, {"role": "body", "size_pt": 18, "count": 100}])
    assert type_roles["heading"]["relative_to_body"] == 2
    assert type_roles["heading"]["family"] == "Noto Sans KR"


def test_source_identity_v2_styles_actual_raster_table_and_preserves_legacy_snapshot():
    from copy import deepcopy
    import xml.etree.ElementTree as ET
    from services.svg_generation_admission import pin_svg_style
    from services.svg_source_layout import SvgLayoutPlan
    from services.source_content.raster_free_composition import assemble_free_raster_svg
    from services.source_content.raster_redesign_tree import Item
    from services.source_content.raster_contracts import RasterTargetCanvas
    from utils.svg_template_rules import reference_template_snapshot

    old_style = {"style_id": "default", "digest": "sha256:legacy", "tokens": {
        "background": "#111827", "foreground": "#FFFFFF", "primary": "#FFD900", "accent": "#FFD900"}}
    legacy = reference_template_snapshot(old_style, 840, 1188)
    frozen = deepcopy(old_style)
    assert pin_svg_style(SimpleNamespace(_svg_style_snapshot=old_style, source_mode=None)) is old_style
    modern = reference_template_snapshot({**old_style, "source_identity_policy": "svg-source-identity/v2"}, 840, 1188)
    assert old_style == frozen and reference_template_snapshot(old_style, 840, 1188) == legacy
    assert legacy["version"] == "svg-source-design-reference/v1" and modern["version"] == "svg-source-design-reference/v2"
    assert modern["tokens"]["text"] == "#FFFFFF" and modern["table"]["body_fill"] == "#111827"
    assert modern["table"]["header_text"] != "#FFFFFF"
    item = Item("table", SimpleNamespace(region_id="approved-table", role="table"), rows=(("Label", "Value"), ("Revenue", "42")))
    font = modern["fonts"]["table"]
    context = dict(source_template=modern, source_unit_id="raster-page-1", page_digest="source-page",
                   width=840, height=1188, minimum_font_size_px=14)
    plan = SvgLayoutPlan(source_unit_id=context["source_unit_id"], page_digest=context["page_digest"],
        width=840, height=1188, background=modern["tokens"]["background"], template_digest=modern["digest"], layout_id="free-svg",
        placements=[dict(block_id="approved-table", x=40, y=100, width=700, height=400,
                         font_family=font["family"], font_weight=str(font["weight"]), font_size=24)])
    svg, bindings = assemble_free_raster_svg(SimpleNamespace(target_canvas=RasterTargetCanvas()), 0, plan, context, [item])
    nodes = list(ET.fromstring(svg).iter())
    texts = [node for node in nodes if node.tag.endswith("}text")]
    assert "Revenue" in "".join(ET.fromstring(svg).itertext()) and "42" in "".join(ET.fromstring(svg).itertext())
    assert {node.get("fill") for node in texts[:2]} == {modern["table"]["header_text"]}
    assert {node.get("fill") for node in texts[2:]} == {modern["tokens"]["text"]}
    assert {node.get("stroke") for node in nodes if node.get("stroke")} == {modern["table"]["border"]}
    assert bindings[0]["region_id"] == "approved-table"
