from utils.authored_font_policy import enforce_authored_font_floor
import pytest

from utils.authored_font_policy import requested_font_floor_px


def test_enforce_authored_font_floor_clamps_style_blocks_and_inline_styles():
    html = """<!doctype html><html><head><style>
.body { font-size: 8px; }
.caption { FONT-SIZE: 8.5PT; }
.valid { font-size: 12px; }
.layout-only { font-size: 0; }
</style></head><body>
<p style="color:#111; font-size: 11.9px">Body</p>
<span style='font-size:9pt'>Caption</span>
</body></html>"""

    result = enforce_authored_font_floor(html)

    assert ".body { font-size: 12px; }" in result
    assert ".caption { FONT-SIZE: 9PT; }" in result
    assert ".valid { font-size: 12px; }" in result
    assert ".layout-only { font-size: 0; }" in result
    assert 'style="color:#111; font-size: 12px"' in result
    assert "style='font-size:9pt'" in result


def test_enforce_authored_font_floor_preserves_script_bodies_and_is_idempotent():
    html = """<!doctype html><html><head>
<script>const template = '<p style="font-size:8px">x</p>';</script>
<style>.label{font-size:7px}</style>
</head><body><div class="label">Label</div></body></html>"""

    once = enforce_authored_font_floor(html)
    twice = enforce_authored_font_floor(once)

    assert "const template = '<p style=\"font-size:8px\">x</p>';" in once
    assert ".label{font-size:12px}" in once
    assert twice == once


def test_enforce_authored_font_floor_accepts_frozen_capacity_floor():
    html = """<html><head><style>
.body{font-size:13px}.caption{font-size:10pt}.valid{font-size:16px}
</style></head><body><p style="font-size:14px">Body</p></body></html>"""

    once = enforce_authored_font_floor(html, minimum_px=15)
    twice = enforce_authored_font_floor(once, minimum_px=15)

    assert ".body{font-size:15px}" in once
    assert ".caption{font-size:11.25pt}" in once
    assert ".valid{font-size:16px}" in once
    assert 'style="font-size:15px"' in once
    assert twice == once


def test_enforce_authored_font_floor_uses_independent_px_and_pt_floors():
    html = """<style>
.page{font-size:12px}.exact-floor{font-size:9.5pt}
</style>"""

    result = enforce_authored_font_floor(
        html,
        minimum_px=12.6667,
        minimum_pt=9.5,
    )

    assert ".page{font-size:12.6667px}" in result
    assert ".exact-floor{font-size:9.5pt}" in result


@pytest.mark.parametrize("instruction,expected", [
    ("글짜를 기본 10px로 해줘", 10),
    ("본문 글자 크기는 10px", 10),
    ("font-size: 7.5pt", 10),
    ("body font size 10px", 10),
    ("기본 여백 10px", None),
    ("폰트 0px", None),
    ("본문 10px 말고 크게", None),
    ("", None),
])
def test_only_explicit_typography_sizes_override_defaults(instruction, expected):
    assert requested_font_floor_px(instruction) == expected


def test_user_size_survives_both_authoring_and_capacity_normalization():
    html = '<style>.body{font-size:10px}.title{font-size:32px}</style>'
    first = enforce_authored_font_floor(html, instructions="본문 10px")
    final = enforce_authored_font_floor(first, minimum_px=15, instructions="본문 10px")
    assert first == final == html
    assert '.body{font-size:12px}' in enforce_authored_font_floor(html)


def test_requested_floor_round_trips_to_renderer_and_export_policy():
    from services.authored_local_fit_policy import (
        POLICY_REVISION, font_policy, font_size_allowed, local_fit_policy,
        requested_font_policy_revision,
    )
    from utils.slide_capture_local_fit import policy_from_request, policy_request_html

    revision = requested_font_policy_revision("본문 10px")
    policy = local_fit_policy(author_revision="request-test", original_html_digest="sha256:test",
                              policy_revision=revision)
    restored = policy_from_request(policy_request_html("<html></html>", policy, fit=True))
    assert restored["floor_pt"] == 7.5
    assert font_policy(restored["policy_revision"]).floor_pt == 7.5
    assert font_size_allowed(7.5, 7.5, policy_revision=revision)
    assert not font_size_allowed(7.5, 7.4, policy_revision=revision)
    assert requested_font_policy_revision("간결하게") == POLICY_REVISION
    assert requested_font_policy_revision("본문 18px") == POLICY_REVISION


def test_user_floor_is_frozen_in_deck_capacity():
    from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
    from services.generation_core.authoring.author_deck import prepare_authored_deck
    from services.generation_core.authoring.author_slide import Brand

    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content="개발방법론")])
    plan = prepare_authored_deck(outline, Brand(topic="제안서"), instructions="본문 10px")
    assert plan.capacity_plan.at(0).min_font_px == 10
    assert plan.instructions == "본문 10px"
