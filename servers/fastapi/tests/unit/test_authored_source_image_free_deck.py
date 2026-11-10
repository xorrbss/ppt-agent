"""A source whose pages are text only has no pictures to place or verify.

``plan_slide_source_images`` returns ``None`` -- not an empty list -- to say
that source images are not in play for a deck; the substitution step reads that
same value as "data-asset-id stays inert". The final visibility seal treated it
as a planning failure instead, and operation a53ecc54 lost a 35-slide deck to
it after every earlier gate had passed. The deck's manifest carries 289 text
blocks and no image blocks at all.
"""

from __future__ import annotations


from services.authored_source_images import plan_slide_source_images


def test_a_text_only_source_plans_no_pictures() -> None:
    assert plan_slide_source_images(["첫 장", "둘째 장"], []) is None
