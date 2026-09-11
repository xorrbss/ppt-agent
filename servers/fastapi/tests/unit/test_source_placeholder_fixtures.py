"""Published-deck identity/structure replay and honest failed-run boundaries."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.source_content.placeholder.block_table import table_from_manifest
from services.source_content.placeholder.substitute import expand_placeholders, _text
from services.source_content.source_runs import parse_source_binding_id
from tests.unit.placeholder_fixture_adapter import legacy_incompatibilities, token_version
from services.source_content.source_stamp_dom import deterministic_source_qid
from utils.authored_html_tree.parser_adapter import parse_authored_html
from utils.authored_html_tree.serializer import serialize_authored_html

FIXTURES = Path(__file__).resolve().parents[4] / "artifacts/verbatim-design-measurements-20260911/fixtures"
PUBLISHED = [(run, index) for run in ("run-16", "run-20", "run-21") for index in range(35)]


@pytest.mark.parametrize("run,index", PUBLISHED)
def test_published_stamping_structure_and_canonical_readback_parity(run, index):
    directory = FIXTURES / run / f"{index:02d}"
    html = (directory / "html-render.txt").read_text(encoding="utf-8")
    table = table_from_manifest(json.loads((directory / "manifest.json").read_text(encoding="utf-8")))
    incompatibilities = legacy_incompatibilities(html, table)
    if incompatibilities:
        # These are tested classifications, never a manufactured parity pass.
        assert all(item["kind"] in {"shared_owner", "out_of_order"} for item in incompatibilities)
        assert all(item["sourceBlockId"] in {block.id for block in table} for item in incompatibilities)
        return
    result = expand_placeholders(token_version(html, table), table, context=SimpleNamespace())
    original = parse_authored_html(html, profile="document").root
    parsed = parse_authored_html(result.html, profile="document")
    assert serialize_authored_html(parsed) == result.html
    # Source ownership may coalesce canonically; the authored inline tree stays.
    for before, after in zip(original.iter(), parsed.root.iter(), strict=True):
        assert before.tag == after.tag
        for name, value in before.attrib.items():
            if name not in {"data-source-block-id", "data-qid"}:
                assert after.get(name) == value
    blocks = {block.id: block for block in table}
    observed = set()
    qids = set()
    covered_runs = {block.id: [] for block in table}
    for node in parsed.root.iter():
        binding = node.get("data-source-block-id")
        if not binding:
            continue
        block_id, first, last = parse_source_binding_id(binding)
        if block_id not in blocks:
            continue
        observed.add(block_id)
        qid = node.get("data-qid")
        assert qid == deterministic_source_qid(binding) and qid not in qids
        qids.add(qid)
        projection = blocks[block_id].projection
        span = projection.whole if first is None else (first, last)
        covered_runs[block_id].extend(range(span[0], span[1] + 1))
        assert projection.accepts(*span, _text(node)), binding
    assert observed == set(blocks)
    legacy_ids = {
        parse_source_binding_id(node.get("data-source-block-id"))[0]
        for node in original.iter() if node.get("data-source-block-id")
    }
    assert observed == legacy_ids.intersection(blocks)
    for block in table:
        assert sorted(covered_runs[block.id]) == list(range(block.projection.run_count))


@pytest.mark.parametrize("run,index,block_id", [
    ("run-17", 9, "source-slide:10:text:17"),
    ("run-19", 33, "source-slide:34:text:6"),
    ("run-23", 1, "source-slide:2:text:6"),
])
def test_failed_binding_source_is_constructed_or_missing_is_rejected(run, index, block_id):
    directory = FIXTURES / run / f"{index:02d}"
    table = table_from_manifest(json.loads((directory / "manifest.json").read_text(encoding="utf-8")))
    assert block_id in {block.id for block in table}
    # A NEW token candidate fixes the historical binding problem; it does not
    # make the failed historical captured evidence an admitted artifact.
    html = "".join(f"<p>{{{{q:{block.id}}}}}</p>" for block in table)
    result = expand_placeholders(html, table, context=SimpleNamespace())
    assert result.receipt["blocks"] == len(table)
    with pytest.raises(ValueError, match="source_placeholder_missing"):
        expand_placeholders(html.replace(f"{{{{q:{block_id}}}}}", "altered"), table, context=SimpleNamespace())


@pytest.mark.parametrize("run,index", [
    ("run-13", 22), ("run-18", 30), ("run-14", 2), ("run-19", 2), ("run-19", 12), ("run-19", 33),
])
def test_failed_author_absence_cannot_be_adapted_to_success(run, index):
    directory = FIXTURES / run / f"{index:02d}"
    verdict = json.loads((directory / "verdict.json").read_text(encoding="utf-8"))
    assert verdict["checkpoint"]["author_status"] != "completed"
    assert not (directory / "html-author.txt").exists()
    table = table_from_manifest(json.loads((directory / "manifest.json").read_text(encoding="utf-8")))
    with pytest.raises(ValueError, match="source_placeholder_missing"):
        expand_placeholders("<p>author exhausted</p>", table, context=SimpleNamespace())
    # The author-exhaustion/flagged-placeholder decision stays outside the
    # substitution module. There are no captured metrics for a live verdict.
    assert any("metrics.json" in absent for absent in verdict["absent"])


def test_double_marker_block_uses_existing_accepted_canonical_rendering():
    manifest = json.loads((FIXTURES / "run-16/33/manifest.json").read_text(encoding="utf-8"))
    block = next(block for block in table_from_manifest(manifest) if block.id == "source-slide:34:text:7")
    assert not block.projection.accepts(*block.projection.whole, block.projection.text)
    assert block.projection.accepts(*block.projection.whole, block.text)
    assert "□" not in block.text
    assert "□" in block.full_text


def test_static_inputs_are_selected_by_available_author_html():
    index = json.loads((FIXTURES / "INDEX.json").read_text(encoding="utf-8"))
    available = [entry for entry in index if entry["has_author_html"]]
    assert len(available) == 199
    assert any(entry["run"] == "run-17" and entry["slide"] == 9 for entry in available)
    for entry in available:
        directory = FIXTURES / entry["run"] / f'{entry["slide"]:02d}'
        html = (directory / "html-author.txt").read_text(encoding="utf-8")
        table = table_from_manifest(json.loads((directory / "manifest.json").read_text(encoding="utf-8")))
        # These checkpoints are legacy expanded HTML, not model token output.
        # Do not relabel them token or manufacture placeholders to get a pass.
        with pytest.raises(ValueError, match="source_placeholder_missing"):
            expand_placeholders(html, table, context=SimpleNamespace())
