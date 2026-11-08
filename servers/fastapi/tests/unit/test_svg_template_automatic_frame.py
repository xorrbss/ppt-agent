from services.svg_templates.generation import automatic_frame_id

import pytest


def frame(frame_id, role="body", fields=(), width=10, height=10):
    return {"frame_id": frame_id, "role": role, "dynamic_fields": list(fields),
            "body_region": {"x": 0, "y": 0, "width": width, "height": height}}


CHAPTER = ("title", "chapter_number", "chapter_title", "page_number")


def test_cover_page_takes_cover_frame():
    frames = [frame("a", "body", CHAPTER), frame("c", "cover"), frame("s", "section")]
    assert automatic_frame_id(frames, 0) == "c"


def test_body_prefers_chapter_number_and_title_over_title_only():
    frames = [frame("c", "cover"), frame("t", fields=("title", "chapter_title")),
              frame("n", fields=CHAPTER), frame("plain", width=99, height=99)]
    assert automatic_frame_id(frames, 3) == "n"


def test_equal_fit_prefers_largest_body_region_then_source_order():
    frames = [frame("small", fields=CHAPTER, width=5), frame("big", fields=CHAPTER, width=9),
              frame("big2", fields=CHAPTER, width=9)]
    assert automatic_frame_id(frames, 1) == "big"
    assert automatic_frame_id(frames, 2) == "big"


def test_section_frames_are_not_chosen_for_body_pages():
    frames = [frame("s", "section", CHAPTER), frame("b", fields=("title",))]
    assert automatic_frame_id(frames, 1) == "b"


def test_without_cover_or_body_frames_falls_back_deterministically():
    frames = [frame("s1", "section", ("title",)), frame("s2", "section", CHAPTER)]
    assert automatic_frame_id(frames, 0) == "s2"
    assert automatic_frame_id(frames, 4) == "s2"


def chapter_frame(frame_id, number, fields=CHAPTER, **kwargs):
    value = frame(frame_id, fields=fields, **kwargs)
    value["elements"] = [{"dynamic_key": "chapter_number",
                          "svg": f'<text x="1" y="2">{number}</text>'}]
    return value


def chapters():
    return [frame("c", "cover"), chapter_frame("one", "Ⅰ"), chapter_frame("two", "Ⅱ"),
            chapter_frame("three", "Ⅲ")]


def test_page_takes_the_frame_of_its_chapter():
    assert automatic_frame_id(chapters(), 4, {"number": "Ⅲ", "title": "x"}) == "three"
    assert automatic_frame_id(chapters(), 2, {"number": "2", "title": "x"}) == "two"
    assert automatic_frame_id(chapters(), 2, {"number": "01", "title": "x"}) == "one"


def test_unknown_chapter_or_no_chapter_falls_back_to_best_frame():
    assert automatic_frame_id(chapters(), 4, {"number": "Ⅸ", "title": "x"}) == "one"
    assert automatic_frame_id(chapters(), 4, None) == "one"
    assert automatic_frame_id(chapters(), 4, {"number": "", "title": ""}) == "one"


def test_cover_ignores_chapter_and_cover_less_first_page_is_chapter_free():
    assert automatic_frame_id(chapters(), 0, {"number": "Ⅲ", "title": "x"}) == "c"
    no_cover = chapters()[1:]
    assert automatic_frame_id(no_cover, 0, {"number": "Ⅲ", "title": "x"}) == "one"


def test_chapter_never_beats_a_better_fitting_frame():
    frames = [chapter_frame("weak", "Ⅱ", fields=("title",)), chapter_frame("full", "Ⅰ")]
    assert automatic_frame_id(frames, 3, {"number": "Ⅱ", "title": "x"}) == "full"


def test_same_chapter_frames_tie_on_region_then_order():
    frames = [chapter_frame("a", "Ⅱ", width=5), chapter_frame("b", "Ⅱ", width=9),
              chapter_frame("c", "Ⅱ", width=9)]
    assert automatic_frame_id(frames, 3, {"number": "Ⅱ", "title": "x"}) == "b"


def test_frames_without_a_chapter_tab_are_not_chapter_specific():
    plain = [frame("a", fields=CHAPTER), frame("b", fields=CHAPTER)]
    assert automatic_frame_id(plain, 3, {"number": "2", "title": "x"}) == "a"


def test_chapter_survives_the_page_context_round_trip():
    from services.svg_templates.chapters import chapter_from_context
    context = {"dynamic_values": {"chapter_number": "Ⅲ", "chapter_title": "t"}}
    assert automatic_frame_id(chapters(), 5, chapter_from_context(context)) == "three"


def test_a_saved_context_keeps_the_frame_it_was_published_with(monkeypatch):
    """A page published before chapter frames were chosen per chapter keeps its frame."""
    from services.svg_templates import generation

    frames = {"frame-2": "F2", "frame-5": "F5"}
    selection = {"mode": "template-redesign", "frame_map": {}}
    monkeypatch.setattr(generation, "template_selection", lambda snapshot: selection)
    monkeypatch.setattr(generation, "template_package", lambda sel: {"frames": []})
    monkeypatch.setattr(generation, "template_adaptations", lambda sel: frames)
    monkeypatch.setattr(generation, "unpack_adaptation_fonts", lambda adaptation, package: adaptation)

    class _Adaptation:
        @staticmethod
        def model_validate(value):
            return type("A", (), {"frame": value})

    monkeypatch.setattr(generation, "SvgTemplateFrameAdaptation", _Adaptation)
    recomputed = []
    monkeypatch.setattr(generation, "page_frame", lambda *args, **kwargs: recomputed.append(kwargs) or "AUTO")
    context = {"page_index": 3, "page_count": 10, "frame_id": "frame-2",
               "dynamic_values": {"chapter_number": "Ⅲ", "chapter_title": "수행"}}
    assert generation.context_frame({}, context) == "F2" and not recomputed
    selection["frame_map"] = {"4": "frame-5"}  # a frame map entry still wins
    assert generation.context_frame({}, context) == "AUTO" and recomputed


@pytest.mark.parametrize("background,inset", [(False, False), (True, False), (True, True)])
def test_historical_page_contexts_remain_usable_without_changing_their_proof(monkeypatch, background, inset):
    from copy import deepcopy
    from types import SimpleNamespace
    from models.svg_template_frame import FrameRect
    from services.svg_templates import generation, frame_composition
    from services.svg_page_remake import _template_context
    from utils.hashing import canonical_json_digest

    region = FrameRect(x=20, y=30, width=900, height=500)
    fixed = FrameRect(x=10, y=10, width=5, height=5)
    frame = SimpleNamespace(frame_id="old-frame", digest="frame-digest", dynamic_fields=(),
                            body_region=region, forbidden_regions=(fixed,), source_page=0,
                            font_manifest=SimpleNamespace(assets=[]))
    selection = {"mode": "template-redesign", "frame_map": {}}
    monkeypatch.setattr(generation, "template_selection", lambda snapshot: selection)
    monkeypatch.setattr(generation, "page_frame", lambda *a, **k: frame)
    monkeypatch.setattr(generation, "context_frame", lambda *a, **k: frame)
    monkeypatch.setattr(generation, "template_adaptations", lambda s: {"old-frame": {
        "adaptation_id": "adapt", "digest": "adapt-digest"}})
    monkeypatch.setattr(generation, "template_package", lambda s: {"body_evidence": []})
    monkeypatch.setattr(frame_composition, "render_frame_svg", lambda *a, **k: "")
    monkeypatch.setattr(frame_composition, "body_background", lambda f: {"mean_color": "#FFFFFF"})
    source = "## Original title\nOriginal material"
    # Exact encodings shipped before background guidance, between background
    # guidance and margin insertion, and after margin insertion respectively.
    old = {"schema_version": "svg-template-page/v1", "page_index": 0, "page_count": 1,
           "frame_id": "old-frame", "frame_digest": "frame-digest",
           "adaptation_id": "adapt", "adaptation_digest": "adapt-digest",
           "dynamic_values": {"title": "Original title", "page_number": "1", "section_number": ""},
           "dynamic_fields": [], "content_digest": canonical_json_digest({"content": source}),
           "body_region": {"x": 24.0, "y": 34.0, "width": 892.0, "height": 492.0} if inset else region.model_dump(mode="json"),
           "forbidden_regions": [{"x": 6.0, "y": 6.0, "width": 13.0, "height": 13.0}] if inset else [fixed.model_dump(mode="json")],
           "font_families": [], "font_faces": [], "source_body_evidence": []}
    if background:
        old["body_background"] = {"mean_color": "#FFFFFF"}
    old["digest"] = canonical_json_digest(old)
    preserved = deepcopy(old)
    presentation = SimpleNamespace(theme={"svg": {"styleSnapshot": {"svg_template": selection}}})
    slides = [SimpleNamespace(content={"source": source, "svgReceipt": {"svg_template_page": old}})]
    assert _template_context(presentation, slides, 0)[1] == old == preserved
    slides[0].content["source"] += " changed"
    with pytest.raises(ValueError, match="svg_template_page_context_changed"):
        _template_context(presentation, slides, 0)
    slides[0].content["source"] = source
    old["body_region"]["width"] -= 1
    old["digest"] = canonical_json_digest({k: v for k, v in old.items() if k != "digest"})
    with pytest.raises(ValueError, match="svg_template_page_context_changed"):
        _template_context(presentation, slides, 0)
    selection["page_context_revision"] = 2
    current = generation.template_page_context({}, 0, 1, source)
    assert current["schema_version"] == "svg-template-page/v2"
    with pytest.raises(ValueError, match="svg_template_page_context_invalid"):
        generation.template_page_context({}, 0, 1, source, saved_context=preserved)


def test_a_move_is_checked_against_the_template_frame_at_both_positions(monkeypatch):
    """A v1 context keeps its saved frame, so the move itself must not change the frame."""
    from types import SimpleNamespace
    from services.svg_templates import generation

    frames = [frame("c", "cover"), frame("b", "body", CHAPTER)]

    def page_frame(snapshot, index, count, role=None, chapter=None):
        return SimpleNamespace(frame_id=automatic_frame_id(frames, index, chapter))

    monkeypatch.setattr(generation, "page_frame", page_frame)
    assert generation.same_frame_after_move({}, 3, 6, 4, 6)
    assert not generation.same_frame_after_move({}, 3, 6, 0, 6)  # body page to the cover position
    assert not generation.same_frame_after_move({}, 0, 6, 2, 6)  # cover page into the body
