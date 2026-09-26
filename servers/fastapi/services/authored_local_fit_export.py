"""Read the packaged PPTX before accepting the local-fit output contract."""
from __future__ import annotations

from services.authored_local_fit_receipt_checks import (
    NS,
    LocalFitPageVerificationError as LocalFitPageVerificationError,
    _bounded_page_code,
    _HYBRID_NATIVE_TEXT_ADVISORY_CODES,
    _HYBRID_CHART_ADVISORY_CODES,
    _HYBRID_IMAGE_EVIDENCE_ADVISORY_CODES,
    _fail,
    _relationships,
    _number,
    _runs,
    _check_sizes,
    _check_receipt,
)

import hashlib
import io
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from services.authored_local_fit_evidence import (
    receipt_quality_passed,
)
from services.authored_local_fit_policy import (
    POLICY_REVISION, font_size_allowed,
)


def _full_picture(picture: ET.Element, width: float, height: float) -> None:
    transform = picture.find("p:spPr/a:xfrm", NS)
    if transform is None or any(transform.get(key, "0") not in ("0", "false")
                                for key in ("rot", "flipH", "flipV")):
        _fail("image_transform_mismatch")
    offset, extent = transform.find("a:off", NS), transform.find("a:ext", NS)
    if (_number(offset, "x"), _number(offset, "y"), _number(extent, "cx"),
            _number(extent, "cy")) != (0, 0, width, height):
        _fail("image_placement_mismatch")
    crop = picture.find("p:blipFill/a:srcRect", NS)
    if crop is not None and any(float(value) != 0 for value in crop.attrib.values()):
        _fail("image_crop_mismatch")


def _picture_placement(root: ET.Element, picture: ET.Element) -> dict[str, float]:
    transform = picture.find("p:spPr/a:xfrm", NS)
    if transform is None or any(transform.get(key, "0") not in ("0", "false")
                                for key in ("rot", "flipH", "flipV")):
        _fail("image_transform_mismatch")
    crop = picture.find("p:blipFill/a:srcRect", NS)
    if crop is not None and any(float(value) != 0 for value in crop.attrib.values()):
        _fail("image_crop_mismatch")
    offset, extent = transform.find("a:off", NS), transform.find("a:ext", NS)
    x, y = _number(offset, "x"), _number(offset, "y")
    width, height = _number(extent, "cx"), _number(extent, "cy")
    parents = {child: parent for parent in root.iter() for child in parent}
    parent = parents.get(picture)
    while parent is not None:
        if parent.tag == "{" + NS["p"] + "}grpSp":
            group = parent.find("p:grpSpPr/a:xfrm", NS)
            if group is None or any(group.get(key, "0") not in ("0", "false")
                                     for key in ("rot", "flipH", "flipV")):
                _fail("image_transform_mismatch")
            off, ch_off = group.find("a:off", NS), group.find("a:chOff", NS)
            ext, ch_ext = group.find("a:ext", NS), group.find("a:chExt", NS)
            cx, cy = _number(ch_ext, "cx"), _number(ch_ext, "cy")
            if cx <= 0 or cy <= 0:
                _fail("image_transform_mismatch")
            sx, sy = _number(ext, "cx") / cx, _number(ext, "cy") / cy
            if sx <= 0 or not math.isclose(sx, sy, rel_tol=1e-10):
                _fail("image_transform_mismatch")
            x, y = _number(off, "x") + (x - _number(ch_off, "x")) * \
                sx, _number(off, "y") + (y - _number(ch_off, "y")) * sy
            width, height = width * sx, height * sy
        parent = parents.get(parent)
    return {"x": x / 12700, "y": y / 12700, "widthPt": width / 12700, "heightPt": height / 12700}


def _owner(shape: ET.Element) -> dict[str, Any]:
    identity = shape.find(".//p:cNvPr", NS)
    description = identity.get("descr", "") if identity is not None else ""
    if not description.startswith("authored-vnext:"):
        _fail("native_run_owner_unavailable")
    try:
        result = json.loads(description.removeprefix("authored-vnext:"))
    except ValueError:
        _fail("native_run_owner_unavailable")
    if not isinstance(result, dict):
        _fail("native_run_owner_unavailable")
    return result


def _content_digest(text: str, expected: str) -> str:
    if expected.startswith("sha256:"):
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    value = 2166136261
    encoded = text.encode("utf-16-le", errors="surrogatepass")
    for offset in range(0, len(encoded), 2):
        value = ((value ^ int.from_bytes(encoded[offset:offset + 2], "little")) * 16777619) & 0xFFFFFFFF
    return f"fnv1a32:{value:08x}"

def _match_native_runs(native: list[tuple[str, float]],
                       originals: list[Mapping[str, Any]],
                       emitted: Sequence[Mapping[str, Any]] | None = None,
                       *, policy_revision: str = POLICY_REVISION,
                       enforce_font_policy: bool = True) -> None:
    """Join serializer splits to the original text-node UTF-16 intervals."""
    encoded = "".join(text for text, _ in native).encode("utf-16-le", errors="surrogatepass")
    intervals = []
    offset = 0
    source_map = {(run.get("owner_qid"), run.get("text_node_ordinal")): run for run in originals}
    correspondence = []
    if emitted:
        for item in emitted:
            original = source_map.get((item.get("source_owner_qid"), item.get("source_text_node_ordinal")))
            if original is None:
                _fail("native_run_baseline_unavailable")
            correspondence.append((original, int(item.get("utf16_length", 0)), str(item.get("content_digest", ""))))
    else:
        correspondence = [(original,
            int(original.get("end_offset", 0)) - int(original.get("start_offset", 0)),
            str(original.get("content_digest", ""))) for original in originals]
    for original, length, expected in correspondence:
        if length <= 0:
            _fail("native_run_baseline_unavailable")
        value = encoded[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="surrogatepass")
        if not expected or _content_digest(value, expected) != expected:
            _fail("native_run_content_mismatch")
        intervals.append((offset, offset + length, original))
        offset += length
    if offset * 2 != len(encoded):
        _fail("native_run_content_mismatch")
    cursor = 0
    for text, size in native:
        end = cursor + len(text.encode("utf-16-le", errors="surrogatepass")) // 2
        owners = [run for left, right, run in intervals if min(end, right) > max(cursor, left)]
        if not owners:
            _fail("native_run_baseline_unavailable")
        if enforce_font_policy and not all(
            font_size_allowed(float(run["original_pt"]), size,
                              policy_revision=policy_revision)
            for run in owners
        ):
            _fail("font_floor_or_shrink_limit")
        cursor = end

def _hybrid_text(root: ET.Element, runs: list[Mapping[str, Any]],
                 observation: Mapping[str, Any] | None = None, *,
                 policy_revision: str = POLICY_REVISION,
                 enforce_font_policy: bool = True) -> int:
    by_owner: dict[str, list[Mapping[str, Any]]] = {}
    for run in runs:
        by_owner.setdefault(str(run.get("owner_qid", run.get("qid", ""))), []).append(run)
    measured = 0

    def walk(container: ET.Element, scale: float = 1) -> None:
        nonlocal measured
        for shape in container:
            if shape.tag == "{" + NS["p"] + "}grpSp":
                transform = shape.find("p:grpSpPr/a:xfrm", NS)
                if transform is None or transform.get("rot", "0") != "0":
                    _fail("unverifiable_font_size")
                extent, child = transform.find("a:ext", NS), transform.find("a:chExt", NS)
                sx, sy = _number(extent, "cx") / _number(child, "cx"), _number(extent, "cy") / _number(child, "cy")
                if sx <= 0 or not math.isclose(sx, sy, rel_tol=1e-10):
                    _fail("unverifiable_font_size")
                walk(shape, scale * sx)
                continue
            text_nodes = shape.findall(".//a:t", NS)
            if not any(node.text for node in text_nodes):
                continue
            owner = _owner(shape)
            owner_id = str(owner.get("elementId", ""))
            candidates = by_owner.get(owner_id)
            mappings = (observation or {}).get("native_owners", ())
            mapped = [item for item in mappings if item.get("element_id") == owner_id]
            if len(mapped) == 1:
                owned = mapped[0].get("owner_qids", ())
                candidates = [run for run in runs if run.get("owner_qid") in owned]
            if not candidates:
                # Export ids prefix the declared QID with the slide number.
                candidates = by_owner.get(owner_id.split("-", 1)[-1])
            if not candidates:
                qid = owner_id.split("-", 1)[-1]
                candidates = [run for run in runs if owner_id in run.get("ancestor_qids", ())
                              or qid in run.get("ancestor_qids", ())]
            if not candidates:
                _fail("native_run_baseline_unavailable")
            native_values = []
            for body in shape.findall(".//p:txBody", NS) + shape.findall(".//a:txBody", NS):
                body_scale = scale
                autofit = body.find("a:bodyPr/a:normAutofit", NS)
                if body.find("a:bodyPr/a:spAutoFit", NS) is not None:
                    _fail("unverifiable_font_size")
                if autofit is not None:
                    # Unspecified shrink is application-dependent, not 100%.
                    body_scale *= _number(autofit, "fontScale") / 100000
                for paragraph in body.findall("a:p", NS):
                    level = int(paragraph.find("a:pPr", NS).get("lvl",
                        "0")) if paragraph.find("a:pPr", NS) is not None else 0
                    default = paragraph.find("a:pPr/a:defRPr", NS)
                    if default is None or default.get("sz") is None:
                        default = body.find(f"a:lstStyle/a:lvl{level + 1}pPr/a:defRPr", NS)
                    for native in paragraph:
                        text = native.find("a:t", NS)
                        if text is None or not text.text:
                            continue
                        properties = native.find("a:rPr", NS)
                        if properties is None or properties.get("sz") is None:
                            properties = default
                        size = _number(properties, "sz") / 100 * body_scale
                        native_values.append((text.text, size))
                        measured += 1
            if not native_values:
                _fail("unverifiable_font_size")
            _match_native_runs(native_values, candidates,
                               mapped[0].get("emitted_runs") if len(mapped) == 1 else None,
                               policy_revision=policy_revision,
                               enforce_font_policy=enforce_font_policy)

    tree = root.find("p:cSld/p:spTree", NS)
    if tree is None:
        _fail("slide_tree_unavailable")
    walk(tree)
    return measured

def verify_local_fit_pptx(path_or_bytes: str | Path | bytes,
                          receipts: Sequence[Mapping[str, Any]], *, mode: str = "fidelity",
                          pngs: Sequence[bytes] | None = None,
                          export_evidence: Mapping[str, Any] | None = None,
                          collect_hybrid_page_failures: bool = False,
                          strict_quality: bool = True) -> dict[str, Any]:
    from services.authored_local_fit_export_evidence import (
        load_export_evidence, validate_observation, validate_raster, validate_local_image,
        validate_raster_fragments,
    )
    payload = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    evidence = load_export_evidence(path_or_bytes, payload, receipts, export_evidence)
    native_runs = 0
    deck_policy_revision: str | None = None
    review_pages: list[int] = []
    failed_pages: list[dict[str, object]] = []
    hybrid_quality_advisories: list[dict[str, object]] = []

    def advisory(index: int, error: ValueError) -> None:
        hybrid_quality_advisories.append({
            "slideIndex": index,
            "code": _bounded_page_code(error),
        })
        if index not in review_pages:
            review_pages.append(index)

    def image_evidence_advisory(index: int, error: ValueError) -> bool:
        if (not strict_quality
                and _bounded_page_code(error)
                in _HYBRID_IMAGE_EVIDENCE_ADVISORY_CODES):
            advisory(index, error)
            return True
        return False

    with ZipFile(io.BytesIO(payload)) as archive:
        presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
        dimensions = presentation.find("p:sldSz", NS)
        width, height = _number(dimensions, "cx"), _number(dimensions, "cy")
        relations = _relationships(archive, "ppt/presentation.xml")
        slides = presentation.findall("p:sldIdLst/p:sldId", NS)
        if len(slides) != len(receipts):
            _fail("slide_count_mismatch")
        for index, (slide, receipt) in enumerate(zip(slides, receipts, strict=False)):
            policy_revision = _check_receipt(receipt)
            quality_pass = receipt_quality_passed(receipt)
            if not quality_pass:
                review_pages.append(index)
            if deck_policy_revision is None:
                deck_policy_revision = policy_revision
            elif deck_policy_revision != policy_revision:
                _fail("policy_identity_mismatch")
            if receipt.get("slideIndex") != index:
                _fail("slide_ownership_mismatch")
            manifest = receipt.get("export_scale_manifest", {})
            if (width, height) != (float(manifest.get("width_pt", 0)) * 12700,
                                   float(manifest.get("height_pt", 0)) * 12700):
                _fail("output_scale_mismatch")
            runs = _runs(receipt, required=quality_pass)
            if quality_pass:
                try:
                    _check_sizes(runs, policy_revision=policy_revision)
                except ValueError as error:
                    if (not strict_quality
                            and _bounded_page_code(error)
                            in _HYBRID_NATIVE_TEXT_ADVISORY_CODES):
                        advisory(index, error)
                        quality_pass = False
                    else:
                        raise
            try:
                part = relations[slide.attrib["{" + NS["r"] + "}id"]]
                root = ET.fromstring(archive.read(part))
                pictures = root.findall(".//p:pic", NS)
                slide_evidence = evidence["slides"][index] if evidence is not None else None
                if slide_evidence is not None and quality_pass and strict_quality:
                    validate_observation(
                        slide_evidence.get("observation"), runs, manifest,
                        policy_revision=policy_revision,
                        enforce_font_policy=True,
                    )
                image_relations = _relationships(archive, part)
                backgrounds = root.findall("p:cSld/p:bg/p:bgPr/a:blipFill", NS)
                blips = []
                placements = []
                for picture in pictures:
                    if mode == "fidelity":
                        _full_picture(picture, width, height)
                    placements.append(_picture_placement(root, picture))
                    blips.append(picture.find("p:blipFill/a:blip", NS))
                for background in backgrounds:
                    if background.find("a:stretch/a:fillRect", NS) is None:
                        _fail("image_placement_mismatch")
                    crop = background.find("a:srcRect", NS)
                    if crop is not None and any(float(value) != 0 for value in crop.attrib.values()):
                        _fail("image_crop_mismatch")
                    blips.append(background.find("a:blip", NS))
                    placements.append({"x":0, "y":0, "widthPt":width / 12700, "heightPt":height / 12700})
                image_digests = []
                image_payloads = []
                for blip in blips:
                    rel = blip.get("{" + NS["r"] + "}embed", "") if blip is not None else ""
                    image_path = image_relations.get(rel)
                    if not image_path:
                        _fail("raster_evidence_mismatch")
                    image_payload = archive.read(image_path)
                    image_payloads.append(image_payload)
                    image_digests.append("sha256:" + hashlib.sha256(image_payload).hexdigest())
                if mode == "fidelity":
                    if len(image_digests) != 1 or root.findall(".//a:t", NS):
                        _fail("fidelity_content_mismatch")
                    expected = ("sha256:" + hashlib.sha256(pngs[index]).hexdigest()
                                if pngs is not None else receipt.get("png_digest"))
                    if (not expected or image_digests[0] != expected
                            or expected != receipt.get("png_digest")):
                        _fail("raster_evidence_mismatch")
                    if slide_evidence is not None and strict_quality:
                        validate_raster(
                            image_digests[0], slide_evidence, runs, manifest,
                            policy_revision=policy_revision,
                            enforce_font_policy=quality_pass and strict_quality,
                        )
                elif mode == "hybrid":
                    try:
                        native_runs += _hybrid_text(
                            root, runs, (slide_evidence or {}).get("observation"),
                            policy_revision=policy_revision,
                            enforce_font_policy=quality_pass and strict_quality,
                        )
                    except ValueError as error:
                        if (not strict_quality
                                and _bounded_page_code(error)
                                in _HYBRID_NATIVE_TEXT_ADVISORY_CODES):
                            advisory(index, error)
                        else:
                            raise
                    fragment_indexes = validate_raster_fragments(
                        list(zip(image_digests, placements, image_payloads, strict=True)),
                        slide_evidence, runs, manifest, policy_revision=policy_revision,
                        enforce_font_policy=quality_pass and strict_quality,
                    )
                    for image_index, (image_digest,
                        placement) in enumerate(zip(image_digests, placements, strict=True)):
                        if image_index in fragment_indexes:
                            continue
                        full = placement == {"x":0, "y":0, "widthPt":width / 12700, "heightPt":height / 12700}
                        is_local = any(item.get("pngDigest") == image_digest
                                       for item in (slide_evidence or {}).get("local_images", ()))
                        if not full or is_local:
                            try:
                                validate_local_image(
                                    image_digest, placement, slide_evidence, runs, manifest,
                                    policy_revision=policy_revision,
                                    enforce_font_policy=quality_pass and strict_quality,
                                )
                            except ValueError as error:
                                if not image_evidence_advisory(index, error):
                                    raise
                            continue
                        if (slide_evidence is None and len(image_digests) == 1
                                and not root.findall(".//a:t", NS)
                                and image_digest == receipt.get("png_digest")):
                            continue
                        try:
                            validate_raster(
                                image_digest, slide_evidence, runs, manifest,
                                policy_revision=policy_revision,
                                enforce_font_policy=quality_pass and strict_quality,
                            )
                        except ValueError as error:
                            if not image_evidence_advisory(index, error):
                                raise
                    from services.authored_local_fit_chart_readback import verify_chart_labels
                    charts = root.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/chart}chart")
                    if charts and root.findall(".//p:grpSp", NS):
                        error = ValueError(
                            "authored_local_fit_export:chart_group_transform_unverifiable"
                        )
                        if not strict_quality:
                            advisory(index, error)
                            quality_pass = False
                        else:
                            raise error
                    for chart in charts:
                        chart_path = image_relations.get(chart.get("{" + NS["r"] + "}id", ""))
                        if not chart_path:
                            _fail("chart_font_evidence_unavailable")
                        chart_payload = archive.read(chart_path)
                        if not quality_pass:
                            continue
                        try:
                            native_runs += verify_chart_labels(
                                chart_payload, runs,
                                policy_revision=policy_revision,
                            )
                        except ValueError as error:
                            if (not strict_quality
                                    and _bounded_page_code(error)
                                    in _HYBRID_CHART_ADVISORY_CODES):
                                advisory(index, error)
                            else:
                                raise
                else:
                    _fail("unsupported_export_mode")
            except ValueError as error:
                if str(error) == "authored_local_fit_export:invalid_relationship":
                    raise
                if mode != "hybrid" or not collect_hybrid_page_failures:
                    raise
                failure = {"slideIndex": index, "code": _bounded_page_code(error)}
                from services.authored_local_fit_export_evidence import RasterFragmentVerificationError
                if isinstance(error, RasterFragmentVerificationError):
                    failure["diagnostics"] = error.diagnostics
                failed_pages.append(failure)
    if failed_pages:
        raise LocalFitPageVerificationError(failed_pages)
    quality_passed = not review_pages
    return {"policy_revision": deck_policy_revision or POLICY_REVISION,
            "status": "review-required" if hybrid_quality_advisories else "passed", "mode": mode,
            "documentStatus": "completed", "qualityPass": quality_passed,
            "reviewPages": review_pages,
            "qualityAdvisories": hybrid_quality_advisories,
            "slides": len(receipts), "native_runs": native_runs,
            "pptx_digest": "sha256:" + hashlib.sha256(payload).hexdigest()}
