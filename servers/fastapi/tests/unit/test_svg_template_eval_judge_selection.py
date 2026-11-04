"""Per-template judge references and anchors of scripts/svg_template_eval/request_builder.py (synthetic)."""
import importlib.util
import json
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parents[2] / "scripts" / "svg_template_eval"
sys.path.insert(0, str(_DIR))
_spec = importlib.util.spec_from_file_location("svg_template_eval_request_builder", _DIR / "request_builder.py")
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)


def _manifest(tmp_path, name, judge=None):
    (tmp_path / "pin.json").write_text(json.dumps({"id": name}), encoding="utf-8")
    body = {"id": name, "pin": "pin.json", "canvas_emu": [100, 50]}
    if judge is not None:
        body["judge"] = judge
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return str(path)


def test_manifest_judge_paths_resolve_against_manifest(tmp_path):
    spec = rb.load_template_spec(_manifest(tmp_path, "a", {"references": "refs", "anchor_high": ["r1.png", "/abs/r2.png"],
                                                          "anchor_low": "low.png"}))
    assert spec["judge"]["references"] == str(tmp_path / "refs")
    assert spec["judge"]["anchor_high"] == [str(tmp_path / "r1.png"), "/abs/r2.png"]
    assert spec["judge"]["anchor_low"] == [str(tmp_path / "low.png")]


def test_template_judge_wins_over_global(tmp_path):
    spec = rb.load_template_spec(_manifest(tmp_path, "a", {"references": "refs", "anchor_high": ["h.png"],
                                                          "anchor_low": "l.png"}))
    chosen = rb.judge_selection(spec, "global_refs", "gh.png", "gl.png")
    assert chosen["references"] == str(tmp_path / "refs")
    assert chosen["anchor_high"] == [str(tmp_path / "h.png")] and chosen["anchor_low"] == [str(tmp_path / "l.png")]
    assert set(chosen["sources"].values()) == {"template"}


def test_global_flags_when_template_has_no_judge(tmp_path):
    spec = rb.load_template_spec(_manifest(tmp_path, "b"))
    chosen = rb.judge_selection(spec, "global_refs", "g1.png,g2.png", "gl.png")
    assert chosen == {"references": "global_refs", "anchor_high": ["g1.png", "g2.png"], "anchor_low": ["gl.png"],
                      "sources": {"references": "global", "anchor_high": "global", "anchor_low": "global"}}


def test_partial_template_judge_falls_back_per_key(tmp_path):
    spec = rb.load_template_spec(_manifest(tmp_path, "c", {"anchor_high": ["h.png"]}))
    chosen = rb.judge_selection(spec, "global_refs", None, "gl.png")
    assert chosen["sources"] == {"references": "global", "anchor_high": "template", "anchor_low": "global"}


def test_no_references_anywhere_means_no_judge(tmp_path):
    spec = rb.load_template_spec(_manifest(tmp_path, "d"))
    assert rb.judge_selection(spec, None, "h.png", "l.png") is None


def test_cells_carry_their_template_spec(tmp_path):
    specs = [rb.load_template_spec(_manifest(tmp_path, "a", {"references": "r"})),
             rb.load_template_spec(_manifest(tmp_path, "b"))]
    cells = rb.build_cells(rb.list_inputs()[:1], specs)
    picks = {cell["template"]: rb.judge_selection(cell["spec"], "g", "gh", "gl") for cell in cells}
    assert picks["a"]["sources"]["references"] == "template" and picks["b"]["references"] == "g"
