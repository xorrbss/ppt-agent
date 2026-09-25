from __future__ import annotations

import asyncio

from services.authored_presentation_generation import _require_source_verbatim_bindings
from services.authored_vqa_source_integrity import source_vqa_candidate_integrity_passes


BLOCK_ID = "source-slide:10:text:17"
SOURCE_TEXT = "□ 인스타 프로필  □ 블로그 방향  □ 상품  □ 가격  □ 타깃"
QID = "source-block-3e464ca90148cc93"


def checklist_html(*, include_target: bool) -> str:
    target = (
        '<div class="check-item" data-semantic-role="body" data-qid="q-check-5">'
        "□ 타깃</div>\n"
        if include_target
        else '<div class="check-item" data-semantic-role="decoration" '
        'data-qid="q-check-5"></div>'
    )
    return (
        '<div class="check-list" data-qid="'
        + QID
        + '" data-semantic-role="decoration" data-source-block-id="'
        + BLOCK_ID
        + '">'
        '<div class="check-item" data-semantic-role="body" data-qid="q-check-1">'
        "□ 인스타 프로필</div>\n"
        '<div class="check-item" data-semantic-role="body" data-qid="q-check-2">'
        "□ 블로그 방향</div>\n"
        '<div class="check-item" data-semantic-role="body" data-qid="q-check-3">'
        "□ 상품</div>\n"
        '<div class="check-item" data-semantic-role="body" data-qid="q-check-4">'
        "□ 가격</div>\n"
        + target
        + "</div>"
    )


def test_post_vqa_rejects_run17_shape_with_one_missing_nested_source_item():
    bindings = (((BLOCK_ID, SOURCE_TEXT),),)

    async def validator(_index, html, _metrics):
        _require_source_verbatim_bindings([html], bindings)

    damaged = checklist_html(include_target=False)
    assert damaged.count(f'data-qid="{QID}"') == 1
    assert 'data-semantic-role="decoration"' in damaged
    assert not asyncio.run(
        source_vqa_candidate_integrity_passes(9, damaged, {}, validator)
    )
    assert asyncio.run(
        source_vqa_candidate_integrity_passes(
            9, checklist_html(include_target=True), {}, validator
        )
    )
