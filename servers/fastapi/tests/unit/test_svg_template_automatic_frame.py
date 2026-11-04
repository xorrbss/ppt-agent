from services.svg_templates.generation import automatic_frame_id


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
