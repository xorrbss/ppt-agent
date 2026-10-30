"""Offline admission-to-native proof; optional captured inputs never required in CI."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from lxml import etree
from pptx import Presentation
import pytest

from models.svg_template_frame import SvgTemplateFontAsset, SvgTemplatePackage
from services.svg_templates.frame import extract_template_package
from services.svg_templates.frame_attestation import template_furniture_receipt
from services.svg_templates.frame_composition import adapt_frame, compose_frame_svg
from services.svg_templates.frame_fonts import artifact_digest, bundled_font_assets, font_validation_scope
from services.svg_templates.frame_native_text import A, P, NS, NativeFixedTextExport, _structure, validated_parts
from services.svg_templates.generation import template_page_context
from services.svg_export_bundle import build_svg_export_bundle
from utils.hashing import canonical_json_digest
from workers.public_svg_pptx_convert import convert_pptx


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def test_saved_template_page_rejoins_publication_manifest(monkeypatch):
    from services.generation_core.drivers.svg.publish import svg_slide_content
    from services.svg_document_mutations import validate_saved_template_pages
    from services.svg_templates import generation

    svg = '<svg xmlns="http://www.w3.org/2000/svg"/>'
    manifest = {"screenPlacement": {"sha256": "a" * 64}, "slideRevision": "revision-1"}
    payload = {"svg_content": svg, "quality": {}, "svgAssetManifest": manifest}
    content = svg_slide_content(payload, role="content", source="Slide title", pt_per_px=0.75)
    assert "svgAssetManifest" not in content["svgReceipt"]
    observed = []

    def validate(_snapshot, pages, outline):
        observed.append((pages[0][0]["svgAssetManifest"], outline.slides[0].content))

    monkeypatch.setattr(generation, "validate_template_publication", validate)
    slide = SimpleNamespace(index=0, content=content, svg_content=svg)
    validate_saved_template_pages({"svg_template": {"mode": "template-redesign"}}, [slide])
    assert observed == [(manifest, "Slide title")]


def _portable_source(path, family):
    deck = Presentation()
    deck.slide_width, deck.slide_height = 12192000, 6858000
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    # Independent physical reference: original header/footer boxes and explicit
    # layout are authored before extraction or converter code sees the fixture.
    for text, y, size in (("Fixed header", 182880, 1800), ("Fixed footer", 6400800, 1200)):
        shape = slide.shapes.add_textbox(457200, y, 7315200, 457200)
        sp = etree.fromstring(f'''<p:spPr xmlns:p="{P}" xmlns:a="{A}"><a:xfrm><a:off x="457200" y="{y}"/>
          <a:ext cx="7315200" cy="457200"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>''')
        body = etree.fromstring(f'''<p:txBody xmlns:p="{P}" xmlns:a="{A}"><a:bodyPr wrap="none" anchor="t"
          lIns="0" rIns="0" tIns="0" bIns="0"><a:noAutofit/></a:bodyPr><a:lstStyle/><a:p><a:pPr algn="l">
          <a:lnSpc><a:spcPct val="100000"/></a:lnSpc><a:spcBef><a:spcPts val="0"/></a:spcBef>
          <a:spcAft><a:spcPts val="0"/></a:spcAft></a:pPr><a:r><a:rPr sz="{size}" b="0" i="0">
          <a:solidFill><a:srgbClr val="202020"/></a:solidFill><a:latin typeface="{family}"/>
          <a:ea typeface="{family}"/><a:cs typeface="{family}"/></a:rPr><a:t>{text}</a:t></a:r></a:p></p:txBody>''')
        for old in list(shape._element):
            if old.tag in {f"{{{P}}}spPr", f"{{{P}}}txBody"}:
                shape._element.remove(old)
        shape._element.extend((sp, body))
    deck.save(path)


def _case(source_path, assets, output, actual_svg=None, frame_reviews=None,
          package_transform=None, composed_check=None):
    before = source_path.read_bytes()
    package = extract_template_package(source_path, font_assets=assets, native_fixed_text_layout=True,
                                       frame_reviews=frame_reviews)
    assert package.schema_version.endswith("/v2") and package.compiler_version == "ooxml-frame/2"
    if package_transform is not None:
        package = package_transform(package)
    frame = package.frames[0]
    assert not any(issue.severity == "blocked" for issue in frame.issues)
    fixed = [element for element in frame.elements if element.kind == "text"]
    assert len(fixed) == 2
    adaptation = adapt_frame(frame, frame.source_canvas)
    assert adaptation.frame == frame
    changed_canvas = frame.source_canvas.model_copy(update={
        "physical_size": frame.source_canvas.physical_size.model_copy(update={"height_emu": 6858001})})
    with pytest.raises(ValueError, match="canvas_adaptation_unsupported"):
        adapt_frame(frame, changed_canvas)
    selection = {"schema_version": "svg-template-selection/v1", "mode": "template-redesign",
                 "package": package.model_dump(mode="json"), "frame_map": {"1": frame.frame_id},
                 "adaptations": {frame.frame_id: adaptation.model_dump(mode="json")}}
    if frame.schema_version.endswith("/v3"):
        selection["template_pin"] = {"templateId": "00000000-0000-0000-0000-000000000001",
                                     "revision": 1, "digest": package.digest}
    selection["digest"] = canonical_json_digest(selection)
    snapshot = {"svg_template": selection}
    page = template_page_context(snapshot, 0, 1, "Slide title")
    family = frame.font_manifest.assets[0].family
    body_source = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        f'<text id="body-test" x="300" y="300" font-family="{family}" font-size="24" fill="#202020">Body content</text></svg>')
    if actual_svg is not None:
        # The immutable old full SVG belongs to v1. Reuse only its unowned body
        # in a distinct future v2 composition; never retag the old pin/artifact.
        with pytest.raises(ValueError, match="furniture_mismatch"):
            template_furniture_receipt(actual_svg, snapshot, width=1280, height=720, index=0, count=1, page_context=page)
        actual_root = etree.fromstring(actual_svg.encode())
        body = next(node for node in actual_root if node.get("id") == "svg-system-template-body")
        body_root = etree.Element(actual_root.tag, attrib=dict(actual_root.attrib), nsmap=actual_root.nsmap)
        body_root.extend(deepcopy(list(body)))
        body_source = etree.tostring(body_root, encoding="unicode")
    source = compose_frame_svg(adaptation, body_source, dynamic_values={})
    if actual_svg is not None:
        assert source != actual_svg
    receipt = template_furniture_receipt(source, snapshot, width=1280, height=720, index=0, count=1, page_context=page)
    binding = receipt["generated_template_frame"]
    assert binding["schema_version"] == "svg-template-furniture/v" + frame.schema_version.rsplit("/v", 1)[1]
    if composed_check is not None:
        composed_check(package, source, snapshot, page, receipt)
    mapping = binding["native_fixed_text"]
    assert len(mapping) == 2
    claim = SimpleNamespace(expected_revision=1, presentation_id="offline-fixture")
    bundle_args = dict(claim=claim, slide_refs=[{"sha256": hashlib.sha256(source.encode()).hexdigest()}],
        source_digests=[hashlib.sha256(source.encode()).hexdigest()], notes=[""],
        canvas=frame.source_canvas.model_dump(mode="json"), export_format="pptx", font_embedding=True,
        assets=[None], font_manifest=frame.font_manifest)
    bundle = build_svg_export_bundle(**bundle_args, system_furniture=[receipt])
    bundle = json.loads(json.dumps(bundle))
    changed_authority = deepcopy(receipt)
    changed_value = changed_authority["generated_template_frame"]["native_fixed_text"][next(iter(mapping))]
    changed_sp = etree.fromstring(changed_value["sp_pr_xml"].encode())
    changed_off = changed_sp.find("a:xfrm/a:off", NS)
    changed_off.set("x", str(int(changed_off.get("x")) + 1))
    changed_value["sp_pr_xml"] = etree.tostring(changed_sp, encoding="unicode")
    # Keep the previous binding digest: the identity must hash the actual
    # native authority payload, not merely copy its claimed digest.
    assert bundle["canonicalExportIdentity"] != build_svg_export_bundle(
        **bundle_args, system_furniture=[changed_authority])["canonicalExportIdentity"]
    with pytest.raises(ValueError, match="binding_mismatch"):
        NativeFixedTextExport([source], [changed_authority], frame.source_canvas, frame.font_manifest)
    altered = deepcopy(receipt)
    altered["generated_template_frame"]["native_fixed_text"].pop(next(iter(mapping)))
    altered["generated_template_frame"]["binding_digest"] = artifact_digest({
        k: v for k, v in altered["generated_template_frame"].items() if k != "binding_digest"})
    assert bundle["canonicalExportIdentity"] != build_svg_export_bundle(
        **bundle_args, system_furniture=[altered])["canonicalExportIdentity"]
    with pytest.raises(ValueError, match="binding_mismatch"):
        NativeFixedTextExport([source], [altered], frame.source_canvas, frame.font_manifest)
    invalid = deepcopy(next(iter(mapping.values())))
    invalid["sp_pr_xml"] = invalid["sp_pr_xml"].replace("<a:noFill", '<a:noFill bad="1"', 1)
    invalid["digest"] = artifact_digest({k: v for k, v in invalid.items() if k != "digest"})
    with pytest.raises(ValueError, match="unsupported"):
        validated_parts(invalid, frame.source_canvas, frame.font_manifest)
    invalid_tail = deepcopy(next(iter(mapping.values())))
    invalid_tail["tx_body_xml"] = invalid_tail["tx_body_xml"].replace("</a:rPr>", "</a:rPr>hidden", 1)
    invalid_tail["digest"] = artifact_digest({k: v for k, v in invalid_tail.items() if k != "digest"})
    with pytest.raises(ValueError, match="unsupported"):
        validated_parts(invalid_tail, frame.source_canvas, frame.font_manifest)
    legacy_downgrade = package.model_dump(mode="json")
    legacy_downgrade.update(schema_version="svg-template-package/v1", compiler_version="ooxml-frame/1")
    with pytest.raises(ValueError, match="version_mismatch"):
        SvgTemplatePackage.model_validate(legacy_downgrade)
    output.mkdir()
    path = output / "slide-001.svg"
    path.write_text(source, encoding="utf-8")
    from services.svg_renderer import _template_manifest_materials
    _manifest, _entries, used = _template_manifest_materials(frame.font_manifest.model_dump(mode="json"), source)
    usage = {}
    for name, weight, _asset in used:
        usage.setdefault(name, set()).add(weight)
    target = output / "native.pptx"
    result = convert_pptx([path], target, [""], frame.source_canvas, font_embedding=True,
        font_manifest=bundle["fontManifest"], font_usage=usage, native_coverage_sources=[source],
        system_furniture=[bundle["slides"][0]["systemFurniture"]])
    assert result["nativeFixedText"] == {"schemaVersion": "svg-template-native-fixed-text-readback/v1", "converted": 2, "complete": True}
    assert result["genericNativeCoverageOoxml"]["passed"] is True
    with ZipFile(BytesIO(before)) as archive:
        original = etree.fromstring(archive.read("ppt/slides/slide1.xml"))
    with ZipFile(target) as archive:
        native = etree.fromstring(archive.read("ppt/slides/slide1.xml"))
    verifier = NativeFixedTextExport([source], [receipt], frame.source_canvas, frame.font_manifest)
    verifier.used = {(0, key) for key in mapping}
    first_native = None
    for element in fixed:
        original_shape = next(shape for shape in original.iter(f"{{{P}}}sp")
            if shape.find("p:nvSpPr/p:cNvPr", NS).get("id") == element.source_shape_id)
        text = "".join(original_shape.find("p:txBody", NS).itertext()).strip()
        # Full native XML equality includes original EMU box/insets/anchor,
        # paragraph/run styling, exact original face aliases and text.
        matches = [shape for shape in native.iter(f"{{{P}}}sp")
                   if shape.find("p:txBody", NS) is not None
                   and _structure(shape.find("p:txBody", NS)) == _structure(original_shape.find("p:txBody", NS))]
        assert text and len(matches) == 1
        assert _structure(matches[0].find("p:spPr", NS)) == _structure(original_shape.find("p:spPr", NS))
        verifier.emitted[(0, element.native_fixed_text.svg_text_id)] = int(matches[0].find("p:nvSpPr/p:cNvPr", NS).get("id"))
        first_native = first_native if first_native is not None else matches[0]
    group = next(first_native.iterancestors(f"{{{P}}}grpSp"))
    offset = group.find("p:grpSpPr/a:xfrm/a:off", NS)
    offset.set("x", str(int(offset.get("x")) + 1))
    tampered = output / "ancestor-tampered.pptx"
    with ZipFile(target) as original_archive, ZipFile(tampered, "w") as changed_archive:
        for member in original_archive.infolist():
            changed_archive.writestr(member, etree.tostring(native) if member.filename == "ppt/slides/slide1.xml"
                                    else original_archive.read(member.filename))
    with pytest.raises(ValueError, match="unsupported"):
        verifier.verify(tampered)
    # Generic unowned text must retain its existing converter result.
    generic = output / "generic.pptx"
    convert_pptx([path], generic, [""], frame.source_canvas, font_manifest=frame.font_manifest)
    def bodies(deck):
        with ZipFile(deck) as archive:
            root = etree.fromstring(archive.read("ppt/slides/slide1.xml"))
        return sorted(repr(_structure(shape.find("p:txBody", NS))) for shape in root.iter(f"{{{P}}}sp")
                      if shape.find("p:txBody", NS) is not None and not any(
                          _structure(shape.find("p:txBody", NS)) == _structure(original_shape.find("p:txBody", NS))
                          for original_shape in original.iter(f"{{{P}}}sp")))
    # Match unowned text by content, because fixed text intentionally differs.
    generic_bodies = bodies(generic)
    assert all(body in generic_bodies for body in bodies(target))
    adapter = NativeFixedTextExport([source], [receipt], frame.source_canvas, frame.font_manifest)
    from svg_to_pptx.drawingml.context import ConvertContext
    text_node = next(node for node in etree.fromstring(source.encode()).iter() if node.get("id") in mapping)
    with pytest.raises(ValueError, match="unsupported"):
        adapter.convert(text_node, ConvertContext(translate_x=1))
    assert adapter.convert(text_node, ConvertContext()) is not None
    with pytest.raises(ValueError, match="unsupported"):
        adapter.convert(text_node, ConvertContext())
    with pytest.raises(ValueError, match="unsupported"):
        adapter.verify(target)
    assert source_path.read_bytes() == before
    return {"source_sha256": hashlib.sha256(before).hexdigest(), "package_digest": package.digest,
            "frame_digest": frame.digest, "font_manifest_digest": frame.font_manifest.digest,
            "svg_sha256": hashlib.sha256(source.encode()).hexdigest(), "binding_digest": binding["binding_digest"],
            "canonical_export_identity": bundle["canonicalExportIdentity"],
            "native_pptx_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "native_pptx_path": str(target), "fixed_text_count": len(fixed), "geometry_error_emu": 0,
            "canonical_product_artifact": False, "office_visual_acceptance": False}


def test_native_fixed_text_versioned_contract(tmp_path):
    legacy = Path(__file__).parents[1] / "fixtures/svg_template_native/legacy-v1-package.json"
    stored = legacy.read_bytes()
    assert hashlib.sha256(stored).hexdigest() == "47b97c607a0cc6ba880eb8d0b6283143f7cacfec55e66ce9fa70f05a48b5d0f2"
    assert _bytes(SvgTemplatePackage.model_validate_json(stored).model_dump(mode="json")) == stored
    assets = tuple(asset for asset in bundled_font_assets() if asset.weight == 400)
    assert len(assets) == 1
    source = tmp_path / "source.pptx"
    _portable_source(source, assets[0].family)
    with font_validation_scope():
        results = {"portable": _case(source, assets, tmp_path / "portable")}
        baseline_path = os.environ.get("PPT_AGENT_NATIVE_FIXED_TEXT_CAPTURED_BASELINE")
        source_path = os.environ.get("PPT_AGENT_NATIVE_FIXED_TEXT_CAPTURED_SOURCE")
        if baseline_path or source_path:
            assert baseline_path and source_path
            baseline_bytes = Path(baseline_path).read_bytes()
            assert hashlib.sha256(baseline_bytes).hexdigest() == "b1442cfb9574c94a2948e17364f96572ce897c829f2a56e8ef2f0ff3346b2edb"
            baseline = json.loads(baseline_bytes)
            package = baseline["presentation"]["theme"]["svg"]["styleSnapshot"]["svg_template"]["package"]
            assert _bytes(SvgTemplatePackage.model_validate(package).model_dump(mode="json", by_alias=True)) == _bytes(package)
            source_path = Path(source_path)
            assert hashlib.sha256(source_path.read_bytes()).hexdigest() == "e30208cf62b6ffcdb2d5e70e57bc41e49361493441355e4a6619c7b5a3a12618"
            svg = baseline["slides"][0]["svg_content"]
            assert hashlib.sha256(svg.encode()).hexdigest() == "4f51876efcbcad0f45fa5337af4986da031d1ce8632b1a846c5272e27f0fb7f9"
            captured_assets = tuple(SvgTemplateFontAsset.model_validate(asset) for asset in package["font_manifest"]["assets"])
            frame = package["frames"][0]
            reviews = {frame["frame_id"]: {"fixed_shape_ids": [element["source_part"] + "#" + element["source_shape_id"]
                for element in frame["elements"] if element["kind"] == "text"],
                "body_region": frame["body_region"], "role": frame["role"]}}
            results["captured"] = _case(source_path, captured_assets, tmp_path / "captured", actual_svg=svg, frame_reviews=reviews)
            assert results["captured"]["package_digest"] != package["digest"]
            assert Path(baseline_path).read_bytes() == baseline_bytes
        proof_path = os.environ.get("PPT_AGENT_NATIVE_FIXED_TEXT_PROOF_OUTPUT")
        if proof_path:
            with Path(proof_path).open("x", encoding="utf-8") as stream:
                json.dump({"schema_version": "native-fixed-text-offline-contract-proof/v1", "cases": results,
                           "old_pin_reinterpreted": False, "live_execution": False}, stream, indent=2)


def test_fixed_glyph_preview_versioned_contract(tmp_path, monkeypatch):
    """Existing immutable PDF, pure geometry and editable native export; no raster/Office."""
    pdf_path = os.environ.get("PPT_AGENT_FIXED_GLYPH_CAPTURED_PDF")
    source_path = os.environ.get("PPT_AGENT_NATIVE_FIXED_TEXT_CAPTURED_SOURCE")
    baseline_path = os.environ.get("PPT_AGENT_NATIVE_FIXED_TEXT_CAPTURED_BASELINE")
    if not (pdf_path and source_path and baseline_path):
        pytest.skip("immutable source/PDF inputs were not supplied")
    from services.svg_templates.frame_glyphs import (
        build_preview_package, source_frame_projection_digest, validate_frame_glyph_authority)
    from services.svg_templates.frame_composition import render_frame_svg
    from services.svg_templates.generation import validate_template_publication, validate_template_publication_page
    from services.svg_templates.frame_fonts import bytes_digest
    from models.svg_template_glyph import SvgTemplateFixedGlyphPreview, glyph_authority_digest
    from utils.svg_template_glyph_materialization import materialize_fixed_glyph_svg
    from utils.svg_validation import validate_svg
    source_path, pdf_path = Path(source_path), Path(pdf_path)
    source_bytes, pdf_bytes = source_path.read_bytes(), pdf_path.read_bytes()
    assert hashlib.sha256(source_bytes).hexdigest() == "e30208cf62b6ffcdb2d5e70e57bc41e49361493441355e4a6619c7b5a3a12618"
    assert hashlib.sha256(pdf_bytes).hexdigest() == "54e7bd53d1ded86ada7f1f178228b30d0795e8b391d17fd2bd2d810bfcdf5ea5"
    baseline_bytes = Path(baseline_path).read_bytes()
    assert hashlib.sha256(baseline_bytes).hexdigest() == "b1442cfb9574c94a2948e17364f96572ce897c829f2a56e8ef2f0ff3346b2edb"
    baseline = json.loads(baseline_bytes)
    pinned = baseline["presentation"]["theme"]["svg"]["styleSnapshot"]["svg_template"]["package"]
    assets = tuple(SvgTemplateFontAsset.model_validate(a) for a in pinned["font_manifest"]["assets"])
    source_frame = pinned["frames"][0]
    reviews = {source_frame["frame_id"]: {"fixed_shape_ids": [e["source_part"] + "#" + e["source_shape_id"]
        for e in source_frame["elements"] if e["kind"] == "text"],
        "body_region": source_frame["body_region"], "role": source_frame["role"]}}
    authority_result = {}
    def transform(package):
        before = _bytes(package.model_dump(mode="json"))
        from services.svg_templates.source_pdf_worker import capture_source_pdf
        from services.svg_templates import source_review
        from utils import get_env
        from utils.renderer_worker_policy import build_worker_environment, assert_no_provider_secrets
        from workers import public_svg_pptx_render as renderer
        environment = build_worker_environment({"PATH": "synthetic-path", "DATABASE_URL": "synthetic-db",
            "OPENAI_API_KEY": "synthetic-key", "USER_CONFIG_PATH": "synthetic-config"})
        assert_no_provider_secrets(environment)
        assert environment["PATH"] == "synthetic-path"
        assert not {"DATABASE_URL", "OPENAI_API_KEY", "USER_CONFIG_PATH"} & environment.keys()
        # Fail before Win32 imports/activation, then prove durable fencing survives a fresh call.
        with monkeypatch.context() as patch:
            patch.setattr(get_env, "get_app_data_directory_env", lambda: str(tmp_path / "capture-store"))
            patch.setattr(source_review, "_require_installed_fonts", lambda _manifest: None)
            patch.setattr(renderer, "_available_memory", lambda: 0)
            scope = dict(operation_id="00000000-0000-0000-0000-000000000001",
                         owner_id="00000000-0000-0000-0000-000000000002", tenant_id="test-tenant")
            with pytest.raises(ValueError, match="capture_failed"):
                capture_source_pdf(source_path, package, **scope)
            markers = list((tmp_path / "capture-store").rglob("once.json"))
            assert len(markers) == 1
            once = markers[0].read_bytes()
            failed = json.loads(markers[0].with_name("receipt.json").read_text())
            assert failed["status"] == "failed_no_retry" and failed["export_calls"] == 0
            assert failed["failure"]["stage"] == "capability_preconditions"
            with pytest.raises(ValueError, match="already_attempted"):
                capture_source_pdf(source_path, package, **scope)
            assert markers[0].read_bytes() == once
            for delta in ({"tenant_id": "different-tenant"},
                          {"owner_id": "00000000-0000-0000-0000-000000000003"},
                          {"operation_id": "00000000-0000-0000-0000-000000000004"}):
                with pytest.raises(ValueError, match="capture_failed"):
                    capture_source_pdf(source_path, package, **{**scope, **delta})
            assert len(list((tmp_path / "capture-store").rglob("once.json"))) == 4
        from pdfminer.pdftypes import PDFStream
        from pdfminer.psparser import LIT
        from services.svg_templates.frame_glyphs import _bounded_stream
        import zlib
        with pytest.raises(ValueError, match="decoded_stream_limit"):
            _bounded_stream(PDFStream({"Filter": LIT("FlateDecode")}, zlib.compress(b"x" * 100)), 32)
        # Deliberate mocked owned-capture boundary. Historical failed receipts are never changed.
        receipt = {"status": "captured", "source_digest": package.source_digest,
            "package_digest": package.digest, "pdf_digest": bytes_digest(pdf_bytes),
            "source_unchanged": True, "owned_job_closed": True, "export_calls": 1,
            "producer_version": "16.0", "producer_build": "20326", "test_capture_boundary": True}
        result = build_preview_package(package, source_path, pdf_bytes, receipt)
        assert _bytes(package.model_dump(mode="json")) == before
        old, frame = package.frames[0], result.frames[0]
        authority = validate_frame_glyph_authority(frame)
        authority_result["value"] = authority
        assert source_frame_projection_digest(frame) == old.digest
        assert render_frame_svg(frame) == render_frame_svg(old)
        assert len(authority.text_objects) == 2
        chars = [c for obj in authority.text_objects for c in obj.characters]
        assert len(chars) == 20 and len({c.unicode for c in chars}) == 16
        assert sum(len(c.path_pdf) for c in chars) == 665
        assert authority.approved_font_digest == "sha256:0086c19e81d293a542e7d75564c645fb58070cc850aefebf8fa1c397858e510c"
        invalid = authority.model_dump(mode="json")
        invalid["text_objects"][0]["characters"][0]["path_pdf"][0][1] = 0
        invalid["authority_digest"] = glyph_authority_digest(invalid)
        with pytest.raises(ValueError, match="clip_not_inert"):
            SvgTemplateFixedGlyphPreview.model_validate(invalid)
        changed = deepcopy(receipt)
        changed["pdf_digest"] = "sha256:" + "0" * 64
        with pytest.raises(ValueError):
            build_preview_package(package, source_path, pdf_bytes, changed)
        with pytest.raises(ValueError, match="version_mismatch"):
            SvgTemplatePackage.model_validate({**result.model_dump(mode="json"), "schema_version": "svg-template-package/v2"})
        return result
    def composed(package, source, snapshot, page, furniture):
        frame = package.frames[0]
        authority = frame.fixed_glyph_preview
        validate_svg(source, width=1280, height=720)
        from services.svg_renderer import svg_preview_html, _svg_fragment, template_manifest_font_css
        resolved = _svg_fragment(svg_preview_html(source, width=1280, height=720,
            font_manifest=frame.font_manifest)).group(1)
        css = template_manifest_font_css(frame.font_manifest, resolved)
        materialized = materialize_fixed_glyph_svg(source, resolved, authority, trusted_font_css=css)
        assert materialized.svg != source and materialized.canonical_digest == bytes_digest(source.encode())
        assert materialized.authority_digest == authority.authority_digest
        assert materialized.svg.count("data-fixed-glyph") >= 1 or "<path" in materialized.svg
        with pytest.raises(ValueError):
            validate_svg(materialized.svg, width=1280, height=720)
        changed = authority.model_copy(update={"authority_digest": "sha256:" + "0" * 64})
        with pytest.raises(ValueError):
            materialize_fixed_glyph_svg(source, resolved, changed, trusted_font_css=css)
        # Actual PNG bytes here are a mock render witness, not a raster fidelity claim.
        png = b"mock-render-witness"
        shared = {"version": "svg-fixed-glyph-preview/v1", "authorityDigest": authority.authority_digest,
            "materializerCodeDigest": authority.materializer_code_digest, "canonicalSvgDigest": bytes_digest(source.encode()),
            "screenDisplaySvgDigest": materialized.display_digest, "screenRenderSvgDigest": materialized.render_digest,
            "qidProjectionDigest": materialized.projection_digest,
            "trustedFontCssDigest": materialized.trusted_font_css_digest, "pngDigest": bytes_digest(png)}
        marker = {**shared, "fontManifestDigest": frame.font_manifest.digest, "screenPlacementDigest": "a" * 64,
            "slideRevision": "test-revision", "templatePin": snapshot["svg_template"]["template_pin"], "frameId": frame.frame_id,
            "frameDigest": frame.digest, "adaptationDigest": page["adaptation_digest"]}
        quality = {"measurement_complete": True, "source_digest": bytes_digest(source.encode()),
            "png_digest": bytes_digest(png), "fixed_glyph_preview": shared,
            "trusted_font_materialization": {"manifest_digest": frame.font_manifest.digest},
            "template_preservation": {"page_context_digest": page["digest"], "frame_digest": frame.digest,
                "adaptation_digest": page["adaptation_digest"], "svg_digest": canonical_json_digest({"svg": source}),
                "body_paint_measured": True}}
        payload = {"svg_content": source, "svg_template_page": page, "system_furniture": furniture,
            "quality": quality, "fixedGlyphPreview": marker, "png_sha256": hashlib.sha256(png).hexdigest(),
            "svgAssetManifest": {"slideRevision": "test-revision", "screenPlacement": {"sha256": "a" * 64}}}
        validate_template_publication_page(snapshot, payload, index=0, count=1, content="Slide title")
        outline = SimpleNamespace(slides=[SimpleNamespace(content="Slide title")])
        validate_template_publication(snapshot, [(payload, png)], outline)
        with pytest.raises(ValueError, match="png_mismatch"):
            validate_template_publication(snapshot, [(payload, png + b"wrong")], outline)
        for key in ("authorityDigest", "materializerCodeDigest", "canonicalSvgDigest", "pngDigest",
                    "screenPlacementDigest", "screenRenderSvgDigest", "qidProjectionDigest", "trustedFontCssDigest",
                    "slideRevision", "frameDigest", "adaptationDigest"):
            changed = deepcopy(payload)
            changed["fixedGlyphPreview"][key] = "invalid"
            with pytest.raises(ValueError, match="identity_mismatch"):
                validate_template_publication_page(snapshot, changed, index=0, count=1, content="Slide title")
        missing = deepcopy(payload)
        missing.pop("fixedGlyphPreview")
        with pytest.raises(ValueError, match="identity_mismatch"):
            validate_template_publication_page(snapshot, missing, index=0, count=1, content="Slide title")
    with font_validation_scope():
        result = _case(source_path, assets, tmp_path / "future-v3", frame_reviews=reviews,
                       package_transform=transform, composed_check=composed)
    assert source_path.read_bytes() == source_bytes and pdf_path.read_bytes() == pdf_bytes
    proof_path = os.environ.get("PPT_AGENT_FIXED_GLYPH_PROOF_OUTPUT")
    if proof_path:
        with Path(proof_path).open("x", encoding="utf-8") as stream:
            json.dump({"schema_version": "fixed-glyph-preview-offline-contract-proof/v1", "case": result,
                "authority_digest": authority_result["value"].authority_digest, "selected_characters": 20,
                "selected_control_points": 665, "mocked_capture_and_png": True,
                "office_calls": 0, "browser_calls": 0, "pdf_raster_calls": 0,
                "preview_precision_accepted": False}, stream, indent=2)
