"""The fragmentation gate's type floor is applied mechanically at authoring time.

Bench runs 47/48 (2026-09-08): the live type-scale prompt already asks for
labels at 18px and forbids text below 13px, and gpt-5.6-sol still wrote
12-14px labels on every slide (591 runs per deck). A prompt cannot make that
model comply; ``enforce_authored_font_floor`` can, before the first render and
without a repair round, so the author paths pass the gate's resolved floor.
"""

from __future__ import annotations

from utils.authored_font_policy import enforce_authored_font_floor
from utils.authored_fragmentation_gate import fit_font_floor_px


def _clear(monkeypatch) -> None:
    for key in (
        "AUTHORED_FRAGMENTATION_GATE_ENABLED",
        "AUTHORED_TYPE_FLOOR_PX",
    ):
        monkeypatch.delenv(key, raising=False)


HTML = (
    '<html><body><style>.chip{font-size:13px}</style>'
    '<p style="font-size:14px">label</p><p style="font-size:18px">body</p>'
    "</body></html>"
)


def test_gate_off_keeps_the_capability_floor(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("AUTHORED_FRAGMENTATION_GATE_ENABLED", "false")
    assert fit_font_floor_px() == 12.0
    assert enforce_authored_font_floor(HTML, minimum_px=fit_font_floor_px()) == HTML


def test_gate_on_raises_labels_to_the_type_floor_before_render(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("AUTHORED_FRAGMENTATION_GATE_ENABLED", "true")
    assert fit_font_floor_px() == 15.0
    clamped = enforce_authored_font_floor(HTML, minimum_px=fit_font_floor_px())
    assert ".chip{font-size:15px}" in clamped
    assert 'style="font-size:15px">label' in clamped
    assert 'style="font-size:18px">body' in clamped


def test_exemplar_obeys_the_gate_floor_when_the_gate_is_on(monkeypatch):
    """The stored exemplar carries 12-14px labels; shown beside a 15px rule it
    must not contradict it, so the block is clamped to the same floor."""

    from utils import authored_style_exemplars as exemplars

    sample = '<html><body><p style="font-size:12px">chip</p><p style="font-size:18px">body</p></body></html>'
    monkeypatch.setattr(exemplars, "load_style_exemplar", lambda style_id: sample)
    _clear(monkeypatch)
    monkeypatch.setenv("AUTHORED_FRAGMENTATION_GATE_ENABLED", "false")
    assert sample in exemplars.exemplar_block("any")
    monkeypatch.setenv("AUTHORED_FRAGMENTATION_GATE_ENABLED", "true")
    block = exemplars.exemplar_block("any")
    assert 'font-size:15px">chip' in block
    assert 'font-size:18px">body' in block
