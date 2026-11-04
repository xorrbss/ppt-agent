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
