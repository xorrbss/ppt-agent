from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from utils.outline_citations import (
    strip_outline_citation_links,
    strip_outline_citations,
)


def test_web_search_citation_keeps_its_label_and_loses_its_url():
    """The shape that failed a real deck: a web-search citation appended to a
    bullet. The author turned the link into an `<a href>` the renderer refuses."""
    bullet = (
        "- 2026년 7월 6.0 공개 "
        "([genspark.ai](https://www.genspark.ai/blog/genspark-ai-workspace-6?utm_source=openai))"
    )
    assert strip_outline_citation_links(bullet) == "- 2026년 7월 6.0 공개 (genspark.ai)"


def test_stripping_returns_the_bullet_to_its_length_budget():
    bullet = (
        "- 웹·슬라이드·영상 제작 "
        "([help.manus.im](https://help.manus.im/en/articles/11711128-what-are-the"
        "-differences-between-chat-mode-and-agent-mode?utm_source=openai))"
    )
    assert len(bullet) > 100
    assert len(strip_outline_citation_links(bullet)) < 40


def test_text_without_a_url_is_returned_unchanged():
    """Including its whitespace: nothing reflows a slide that had no citation."""
    content = "## 제목\n- 첫째  항목 (강조)\n- 둘째 항목\n"
    assert strip_outline_citation_links(content) == content


def test_an_unlabelled_link_falls_back_to_the_host():
    assert strip_outline_citation_links("출처 [](https://manus.im/docs)") == "출처 manus.im"


def test_a_bare_url_becomes_the_site_it_named():
    assert (
        strip_outline_citation_links("자세히는 https://www.genspark.ai/blog/x 참고")
        == "자세히는 genspark.ai 참고"
    )


def test_an_angle_autolink_is_removed_before_it_reads_as_a_tag():
    """`<https://...>` would also reach the HTML validators as an unknown tag."""
    assert strip_outline_citation_links("근거 <https://manus.im/docs>") == "근거 manus.im"


def test_an_image_link_collapses_to_its_alt_text():
    assert (
        strip_outline_citation_links("![도표](https://example.com/chart.png)") == "도표"
    )


def test_a_markdown_title_does_not_survive_the_url():
    assert (
        strip_outline_citation_links('[문서](https://manus.im/docs "Manus 문서")')
        == "문서"
    )


def test_a_parenthesised_bare_url_keeps_its_parentheses():
    assert (
        strip_outline_citation_links("승인 필수 (https://manus.im/docs/browser)")
        == "승인 필수 (manus.im)"
    )


def test_multiple_citations_on_one_line_are_all_stripped():
    line = (
        "- 비교 ([a.com](https://a.com/x)) 및 ([b.com](https://b.com/y?utm_source=openai))"
    )
    assert strip_outline_citation_links(line) == "- 비교 (a.com) 및 (b.com)"


def test_empty_text_is_left_alone():
    assert strip_outline_citation_links("") == ""


def test_the_whole_outline_is_cleaned_slide_by_slide():
    outline = PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content="## 표지\n- 링크 없음"),
            SlideOutlineModel(
                content="## 본문\n- 근거 ([manus.im](https://manus.im/docs))"
            ),
        ]
    )
    cleaned = strip_outline_citations(outline)
    assert cleaned.slides[0].content == "## 표지\n- 링크 없음"
    assert cleaned.slides[1].content == "## 본문\n- 근거 (manus.im)"
    # The original model is not mutated; callers rebind rather than rely on it.
    assert "https://manus.im/docs" in outline.slides[1].content


def test_no_url_scheme_survives_a_real_web_search_outline():
    """The end the renderer cares about: whatever the wording, no `http` reaches
    the slide author, so there is nothing left to turn into a link."""
    slides = [
        "## 젠스파크는 뭐야?\n- 올인원 AI 업무공간\n- 2026년 7월 6.0 공개 "
        "([genspark.ai](https://www.genspark.ai/blog/genspark-ai-workspace-6?utm_source=openai))",
        "## 마누스 핵심 무기\n- 와이드 리서치 병렬 조사\n- 프로젝트로 맥락 재사용 "
        "([manus.im](https://manus.im/docs/features/wide-research?utm_source=openai))",
    ]
    outline = strip_outline_citations(
        PresentationOutlineModel(
            slides=[SlideOutlineModel(content=s) for s in slides]
        )
    )
    for slide in outline.slides:
        assert "http" not in slide.content
        assert "](" not in slide.content
