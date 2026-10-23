"""Pin validation ownership, identity lifetime and immutable memo contents."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gc
import json
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
import weakref
import xml.etree.ElementTree as ET

import pytest

from services import svg_source_layout as layout
from services.source_content.digests import digest_json
from services.svg_source_charts import SOURCE_CHART_CONTRACT
from test_svg_source_layout import manifest_for


def _count_validations(monkeypatch):
    validate = Mock(wraps=layout._validate_source_pages)
    monkeypatch.setattr(layout, "_validate_source_pages", validate)
    return validate


def _table_manifest(count):
    manifest = manifest_for([("table", {"rows": [[{"text": "A"}, {"text": "42"}]]})])
    units = tuple(manifest.units[0].model_copy(update={
        "source_unit_id": f"p{index}", "source_index": index,
        "blocks": (manifest.units[0].blocks[0].model_copy(
            update={"source_block_id": f"p{index}:0"}, deep=True),),
    }) for index in range(count))
    manifest = manifest.model_copy(update={"units": units,
        "counts": manifest.counts.model_copy(update={"source_units": count, "table_cells": count * 2})})
    sealed = manifest.model_dump(mode="json", by_alias=True)
    sealed.pop("manifestDigest")
    return manifest.model_copy(update={"manifest_digest": digest_json(sealed)})


def _table_pages(manifest):
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts

    pages = []
    for index, page in enumerate(manifest.units):
        facts = _whole_page_facts(page, {})
        fact = facts[0]
        ns = "{http://www.w3.org/2000/svg}"
        root = ET.Element(ns + "svg", width="1280", height="720", viewBox="0 0 1280 720")
        group = ET.SubElement(root, ns + "g", id=fact["element_id"])
        for cell in fact["cells"]:
            x = 64 + cell["column"] * 300
            ET.SubElement(group, ns + "rect", x=str(x), y="120", width="300", height="120",
                fill="#FFFFFF", stroke="#334155", **{"stroke-width": "1"})
            ET.SubElement(group, ns + "text", id=cell["element_id"], x=str(x + 4), y="150",
                **{"font-family": "Inter", "font-size": "18", "fill": "#111111"}).text = cell["text"]
        svg, receipt = layout.bind_whole_page_source_svg(ET.tostring(root, encoding="unicode"),
            manifest, index, facts=facts, width=1280, height=720,
            style_snapshot={"digest": "sha256:style", "source_authoring_contract": {
                "version": "svg-source-whole-page/v1"}}, minimum_font_px=12)
        pages.append(({"svg_content": svg, "source_preservation": {
            **receipt, "rendering_verified": True}}, b""))
    return pages


def test_publish_refresh_and_native_tables_share_one_whole_manifest_validation(monkeypatch):
    from services import svg_document_mutations as mutations
    from services.generation_core.drivers.svg.publish import _publish
    from services.svg_source_native_tables import source_table_native_objects

    manifest = _table_manifest(4)
    validate = _count_validations(monkeypatch)
    pages = _table_pages(manifest)
    verify = Mock(wraps=layout.verify_source_receipt)
    monkeypatch.setattr(layout, "verify_source_receipt", verify)
    # Stop after the real publication receipt loop, before any persistence.
    monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: None)
    request = SimpleNamespace(_source_content_svg_reflow=True, _source_content_manifest=manifest)
    with pytest.raises(RuntimeError, match="svg_owner_required"):
        asyncio.run(_publish(request, None, None, "ko", None, None, {}, [], pages))
    assert verify.call_count == len(pages)

    slides = [SimpleNamespace(index=index, svg_content=payload["svg_content"], is_svg=lambda: True,
        content={"svgReceipt": {"source_preservation": payload["source_preservation"]}})
        for index, (payload, _) in enumerate(pages)]
    presentation = SimpleNamespace(theme={"svg": {"sourcePreservation": {
        "schemaVersion": "svg-source-preservation/v1", "manifestDigest": manifest.manifest_digest,
        "pageCount": len(pages)}}})
    assert mutations._preserve_source_pages(presentation, slides,
        SimpleNamespace(width_px=1280, height_px=720), source_manifest=manifest)
    assert verify.call_count == 2 * len(pages)
    for index, (payload, _) in enumerate(pages):
        wire, summary = source_table_native_objects(payload["svg_content"],
            payload["source_preservation"], manifest, index, pt_per_px=.75)
        assert wire and summary["accepted"] == 1
    assert verify.call_count == 3 * len(pages)
    assert validate.call_count == 1
    assert layout.ValidatedSourcePages(manifest).pages_for(None) is manifest.units
    broken = {**pages[0][0]["source_preservation"], "page_digest": "stale"}
    with pytest.raises(ValueError, match="svg_source_receipt_input_mismatch"):
        layout.ValidatedSourcePages(manifest).verify_receipt(pages[0][0]["svg_content"], broken, 0)
    assert validate.call_count == 1


def test_exact_contract_identity_and_contract_mutation(monkeypatch):
    manifest = manifest_for([("text", {"text": "Original"})])
    validate = _count_validations(monkeypatch)
    contract = deepcopy(SOURCE_CHART_CONTRACT)
    for value in (None, contract, dict(reversed(list(contract.items())))):
        for _ in range(2):
            assert layout.source_pages(manifest, chart_contract=value) is manifest.units
    assert validate.call_count == 3
    contract["supportedChartTypes"].append("unsupported")
    for _ in range(2):
        with pytest.raises(ValueError, match="svg_source_chart_contract_invalid"):
            layout.source_pages(manifest, chart_contract=contract)
    assert validate.call_count == 5
    copy = manifest.model_copy(deep=True)
    assert copy == manifest and copy is not manifest
    layout.source_pages(copy)
    assert validate.call_count == 6


def test_contract_without_exact_key_is_always_validated(monkeypatch):
    class Contract(dict):
        pass

    manifest = manifest_for([("text", {"text": "Original"})])
    validate = _count_validations(monkeypatch)
    contract = Contract(SOURCE_CHART_CONTRACT)
    assert layout._exact_json_key(contract) is None
    verifier = layout.ValidatedSourcePages(manifest)
    for _ in range(2):
        verifier.pages_for(contract)
    assert validate.call_count == 2
    assert id(manifest) not in layout._SOURCE_PAGES_MEMO


def test_failures_are_never_memoized(monkeypatch):
    manifest = manifest_for([("text", {"text": "Original"})])
    manifest.units[0].blocks[0].payload["text"] = "Tampered before validation"
    validate = _count_validations(monkeypatch)
    for _ in range(2):
        with pytest.raises(ValueError, match="svg_source_manifest_digest_mismatch"):
            layout.source_pages(manifest)
    assert validate.call_count == 2
    assert id(manifest) not in layout._SOURCE_PAGES_MEMO


def test_manifest_garbage_collection_evicts_entry_even_with_retained_pages():
    manifest = manifest_for([("text", {"text": "Original"})])
    identity, reference = id(manifest), weakref.ref(manifest)
    pages = layout.source_pages(manifest)
    assert identity in layout._SOURCE_PAGES_MEMO
    del manifest
    gc.collect()
    assert reference() is None
    assert identity not in layout._SOURCE_PAGES_MEMO
    assert pages[0].blocks[0].payload["text"] == "Original"


def test_concurrent_callers_validate_once(monkeypatch):
    manifest = manifest_for([("text", {"text": "Original"})])
    validate = _count_validations(monkeypatch)
    barrier = Barrier(8)

    def pages(_):
        barrier.wait()
        return layout.source_pages(manifest)

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(page is manifest.units for page in executor.map(pages, range(8)))
    assert validate.call_count == 1


@pytest.mark.parametrize("mutate", [
    lambda data: data.__setitem__("rows", []),
    lambda data: data.__delitem__("rows"),
    lambda data: data.clear(),
    lambda data: data.pop("rows"),
    lambda data: data.popitem(),
    lambda data: data.setdefault("new", 1),
    lambda data: data.update(new=1),
    lambda data: data.__ior__({"new": 1}),
    lambda data: data["rows"].__setitem__(0, []),
    lambda data: data["rows"].__delitem__(0),
    lambda data: data["rows"].append([]),
    lambda data: data["rows"].clear(),
    lambda data: data["rows"].extend([[]]),
    lambda data: data["rows"].insert(0, []),
    lambda data: data["rows"].pop(),
    lambda data: data["rows"].remove(data["rows"][0]),
    lambda data: data["rows"].reverse(),
    lambda data: data["rows"].sort(),
    lambda data: data["rows"].__iadd__([[]]),
    lambda data: data["rows"].__imul__(0),
    lambda data: data["rows"][0][0].update(text="Changed"),
])
def test_memoized_payload_rejects_every_container_mutator(mutate):
    manifest = _table_manifest(1)
    data = layout.source_pages(manifest)[0].blocks[0].payload
    with pytest.raises(TypeError, match="svg_source_manifest_immutable"):
        mutate(data)


def test_sealing_preserves_json_detaches_aliases_and_deepcopy_stays_editable():
    manifest = _table_manifest(1)
    block = manifest.units[0].blocks[0]
    projection = {"cells": [{"text": "A"}]}
    block = block.model_copy(update={"comparison_projection": projection})
    page = manifest.units[0].model_copy(update={"blocks": (block,)})
    manifest = manifest.model_copy(update={"units": (page,)})
    sealed = manifest.model_dump(mode="json", by_alias=True)
    sealed.pop("manifestDigest")
    manifest = manifest.model_copy(update={"manifest_digest": digest_json(sealed)})
    before = manifest.model_dump(mode="json", by_alias=True)
    alias = block.payload["rows"][0][0]
    pages = layout.source_pages(manifest)
    alias["text"] = "Changed alias"
    projection["cells"][0]["text"] = "Changed alias"
    assert manifest.model_dump(mode="json", by_alias=True) == before
    assert json.loads(manifest.model_dump_json(by_alias=True)) == before
    assert isinstance(block.payload, dict) and isinstance(block.payload["rows"], list)
    with pytest.raises(TypeError, match="svg_source_manifest_immutable"):
        pages[0].blocks[0].comparison_projection["cells"][0]["text"] = "Changed"
    editable = deepcopy(block.payload)
    editable["rows"][0][0]["text"] = "Editable copy"
    assert block.payload["rows"][0][0]["text"] == "A"
