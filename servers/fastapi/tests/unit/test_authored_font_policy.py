from utils.authored_font_policy import enforce_authored_font_floor


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
