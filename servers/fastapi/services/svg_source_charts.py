"""Immutable source-chart admission and exact source-to-native projection proof."""
from copy import deepcopy
from xml.etree import ElementTree as ET

from services.source_content.digests import digest_json
from services.generation_core.native_objects.payload import parse_chart_payload

SOURCE_CHART_CONTRACT = {"version": "svg-source-native-chart/v1",
    "sourceFacts": "pptx-native-chart-facts/v1", "projection": "authored-native-chart/v1",
    "axisPolicy": "automatic-linear-general/v1",
    "supportedChartTypes": ["bar", "column", "pie", "donut"]}


def validate_source_chart_contract(value):
    if value is not None and value != SOURCE_CHART_CONTRACT:
        raise ValueError("svg_source_chart_contract_invalid")
    return value


def admitted_source_chart_contract(request):
    """Select once at source admission; saved identities always own replay."""
    snapshot = getattr(request, "_svg_style_snapshot", None)
    if snapshot is not None:
        from services.svg_authoring_profile import validate_profile_agreement
        validate_profile_agreement(request, snapshot)
        return validate_source_chart_contract(snapshot.get("source_chart_contract"))
    binding = getattr(request, "_svg_source_binding", None)
    if binding is not None:
        return validate_source_chart_contract(binding.get("sourceChartContract"))
    if getattr(request, "_svg_authoring_profile_selected", False):
        return None  # Explicit historical 0-8 selection keeps its old route.
    return deepcopy(SOURCE_CHART_CONTRACT)


def source_chart_payload(block, contract):
    if validate_source_chart_contract(contract) is None:
        raise ValueError("svg_source_chart_unsupported")
    raw = block.payload
    if raw.get("chartType") not in SOURCE_CHART_CONTRACT["supportedChartTypes"]:
        raise ValueError("svg_source_chart_type_unsupported")
    facts = raw.get("nativeSource") or {}
    book = facts.get("workbook") or {}
    if (facts.get("version") != SOURCE_CHART_CONTRACT["sourceFacts"] or facts.get("unsupported") != []
            or not facts.get("chartDigest") or not book.get("digest") or book.get("cacheVerified") is not True):
        raise ValueError("svg_source_chart_unsupported_provenance")
    try:
        payload = parse_chart_payload({key: deepcopy(raw[key]) for key in ("chartType", "categories", "series")})
        if len(set(payload["categories"])) != len(payload["categories"]) or any(not x for x in payload["categories"]):
            raise ValueError("categories_ambiguous")
        if any(not x["name"] for x in payload["series"]):
            raise ValueError("series_identity_missing")
        if payload["chartType"] in {"pie", "donut"} and len(payload["series"]) != 1:
            raise ValueError("pie_series_invalid")
        if any(x <= 0 for item in payload["series"] for x in item["values"]):
            raise ValueError("values_nonprojectable")
    except (ValueError, TypeError, OverflowError, KeyError) as error:
        raise ValueError("svg_source_chart_numeric_invalid") from error
    if facts.get("title"):
        payload["title"] = facts["title"]
    for axis in facts.get("axes", []):
        if axis.get("title"):
            payload["categoryAxis" if axis["kind"] == "catAx" else "valueAxis"] = {"title": axis["title"]}
    try:
        return parse_chart_payload(payload)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("svg_source_chart_metadata_unprojectable") from error


def chart_semantics(payload):
    return {key: deepcopy(payload[key]) for key in ("chartType", "categories", "title", "categoryAxis", "valueAxis") if key in payload} | {
        "series": [{"name": item["name"], "values": item["values"]} for item in payload["series"]]}


def source_chart_record(svg, block, element_id, declaration, contract):
    expected = source_chart_payload(block, contract)
    if (not isinstance(declaration, dict) or declaration.get("id") != element_id
            or declaration.get("kind") != "chart" or not isinstance(declaration.get("raw"), dict)):
        raise ValueError("svg_source_chart_declaration_missing")
    from services.generation_core.drivers.svg.native_publish import _entry, _canvas_size
    root = ET.fromstring(svg)
    group = next((node for node in root.iter() if node.get("id") == element_id), None)
    if group is None:
        raise ValueError("svg_source_chart_group_missing")
    from services.generation_core.drivers.svg.native_geometry import normalize_text
    labels = set(expected["categories"]) | {item["name"] for item in expected["series"]}
    labels.update(expected[key]["title"] for key in ("categoryAxis", "valueAxis") if key in expected)
    if expected.get("title"):
        labels.add(expected["title"])
    # Arbitrary unit/furniture wording must not bypass original source facts.
    for node in group.iter():
        if node.tag.endswith("}text"):
            text = normalize_text("".join(node.itertext()))
            if text not in {normalize_text(label) for label in labels}:
                # Original supported axes use General numeric formatting.
                import re
                if not re.fullmatch(r"[+\-]?[\d,.]+", text):
                    raise ValueError("svg_source_chart_label_mismatch")
    width, height = _canvas_size(root)
    try:
        # Same native projector used at publication/export; exact drawing proof.
        entry = _entry(root, declaration, width=width, height=height, pt_per_px=1.)
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise ValueError("svg_source_native_chart_unprojectable") from error
    if chart_semantics(entry.payload) != chart_semantics(expected):
        raise ValueError("svg_source_chart_numeric_mismatch")
    appearance = declaration["raw"]
    if not {"categoryOrder", "valueLabels", "markers"} <= set(appearance):
        raise ValueError("svg_source_chart_appearance_proof_required")
    return {"source_id": block.source_block_id, "element_id": element_id,
            "content_digest": block.content_digest, "numeric_digest": digest_json(chart_semantics(expected)),
            "native_source_digest": digest_json(block.payload["nativeSource"]),
            "axis_policy": SOURCE_CHART_CONTRACT["axisPolicy"],
            "declaration": deepcopy(declaration), "projection_digest": digest_json(entry.to_wire())}


def verify_source_charts(svg, receipt, page):
    contract = validate_source_chart_contract(receipt.get("source_chart_contract"))
    blocks = {b.source_block_id: b for b in page.blocks if b.kind == "chart"}
    records = receipt.get("source_native_charts") or []
    if not blocks and not records:
        return set(), set()
    if len(records) != len(blocks) or {r.get("source_id") for r in records} != set(blocks):
        raise ValueError("svg_source_chart_coverage_mismatch")
    root = ET.fromstring(svg)
    owned, visible = set(), set()
    for record in records:
        expected = source_chart_record(svg, blocks[record["source_id"]], record["element_id"], record["declaration"], contract)
        if record != expected:
            raise ValueError("svg_source_chart_receipt_mismatch")
        group = next((n for n in root.iter() if n.get("id") == record["element_id"]), None)
        if group is None:
            raise ValueError("svg_source_chart_group_missing")
        owned.update(n.get("id") for n in group.iter() if n.get("id"))
        visible.update(n.get("id") for n in group.iter() if n.tag.endswith("}text"))
    return owned, visible


def source_chart_export_policy(content, sidecar):
    """Carry only sealed source semantics into the normal export bundle."""
    receipt = (content.get("svgReceipt") or {}).get("source_preservation") or {}
    records = receipt.get("source_native_charts") or []
    if not records:
        return None
    validate_source_chart_contract(receipt.get("source_chart_contract"))
    if receipt.get("source_chart_contract") is None:
        raise ValueError("svg_source_chart_export_contract_missing")
    policy = {"contract": deepcopy(receipt["source_chart_contract"]), "svgDigest": receipt["svg_digest"],
              "charts": [{"id": record["element_id"], "numericDigest": record["numeric_digest"]}
                         for record in records]}
    validate_source_chart_export_policy(policy, sidecar)
    return policy


def validate_source_chart_export_policy(policy, sidecar, svg=None):
    if policy is None:
        return set()
    from services.svg_contract import source_digest
    if (not isinstance(policy, dict) or set(policy) != {"contract", "svgDigest", "charts"}
            or validate_source_chart_contract(policy.get("contract")) is None
            or not isinstance(policy["svgDigest"], str) or not policy["svgDigest"].startswith("sha256:")
            or not isinstance(policy["charts"], list) or not 1 <= len(policy["charts"]) <= 32):
        raise ValueError("svg_source_chart_export_policy_invalid")
    if svg is not None and policy["svgDigest"] != source_digest(svg):
        raise ValueError("svg_source_chart_export_svg_mismatch")
    objects = sidecar.by_id() if sidecar is not None else {}
    ids = set()
    for record in policy["charts"]:
        if (not isinstance(record, dict) or set(record) != {"id", "numericDigest"}
                or not isinstance(record["id"], str) or record["id"] in ids):
            raise ValueError("svg_source_chart_export_policy_invalid")
        item = objects.get(record["id"])
        if (item is None or item.kind != "chart"
                or digest_json(chart_semantics(item.payload)) != record["numericDigest"]):
            raise ValueError("svg_source_chart_export_numeric_mismatch")
        ids.add(record["id"])
    return ids


def apply_original_chart_axes(projected):
    """Supported originals have automatic linear General axes, never fixed ones."""
    for axis in projected.get("axes", {}).values():
        for key in ("minimum", "maximum", "major_unit", "minor_unit", "log_base", "reverse"):
            axis.pop(key, None)
