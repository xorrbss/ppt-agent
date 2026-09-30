"""A source whose pages are text only has no pictures to place or verify.

``plan_slide_source_images`` returns ``None`` -- not an empty list -- to say
that source images are not in play for a deck; the substitution step reads that
same value as "data-asset-id stays inert". The final visibility seal treated it
as a planning failure instead, and operation a53ecc54 lost a 35-slide deck to
it after every earlier gate had passed. The deck's manifest carries 289 text
blocks and no image blocks at all.
"""

from __future__ import annotations

import pytest

from services.authored_source_images import plan_slide_source_images
from services.authored_illustrations import validate_visible_source_images


def test_a_text_only_source_plans_no_pictures() -> None:
    assert plan_slide_source_images(["첫 장", "둘째 장"], []) is None


def test_the_visibility_seal_passes_a_slide_offered_nothing() -> None:
    html = (
        "<html><head></head><body>"
        '<h1 data-qid="title" data-semantic-role="title">제목</h1>'
        "</body></html>"
    )
    metrics = {"viewport": {"width": 1280, "height": 720}, "elements": []}
    # An empty offer is the shape the pipeline substitutes for ``None``: there
    # is nothing to place, so there is nothing to fail.
    assert validate_visible_source_images(html, {}, metrics) == html


def test_the_visibility_seal_still_requires_telemetry_when_pictures_exist() -> None:
    html = '<html><body><img data-asset-id="a" src="x"></body></html>'
    with pytest.raises(ValueError):
        validate_visible_source_images(html, {}, None)
