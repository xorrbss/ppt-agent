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
def test_uploaded_artifact_design_reference_uses_signals_and_bounded_examples(tmp_path, format):
    import uuid
    from PIL import Image
    from services.svg_uploaded_design_reference import project_uploaded_design_reference, validate_uploaded_design_reference
    from services.references import ReferenceProviderError
    from services.svg_contract import validate_raster_data_url
    from tests.unit.test_artifact_style_builder import _pptx, _text_pdf
    from utils.hashing import sha256_hex

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
                        await prepare_svg_uploaded_design_reference(request, session, asset_gateway=gateway)
            with _owner(owner):
                async with maker() as session:
                    _binding, path = await gateway.resolve_for_processing(session, asset_id=asset.id, kind="conversation_attachment")
                    path.write_bytes(raw.getvalue() + b"changed")
                    with pytest.raises(ReferenceProviderError):
                        await prepare_svg_uploaded_design_reference(request, session, asset_gateway=gateway)
        finally:
            await engine.dispose()
    asyncio.run(scenario())
