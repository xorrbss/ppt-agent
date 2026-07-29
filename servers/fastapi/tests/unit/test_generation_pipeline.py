"""Layout-index clamp/backfill: out-of-range model picks are fixed deterministically
(not masked by a random layout) so a model error is visible + reproducible."""
from models.presentation_structure_model import PresentationStructureModel
from services.generation_pipeline import _clamp_and_backfill_structure


def test_valid_indices_are_left_unchanged():
    structure = PresentationStructureModel(slides=[0, 2, 1])
    _clamp_and_backfill_structure(structure, total_outlines=3, total_slide_layouts=3)
    assert structure.slides == [0, 2, 1]


def test_out_of_range_indices_fall_back_to_zero():
    # 5 is too big, -1 is negative — both are invalid and get a deterministic 0.
    structure = PresentationStructureModel(slides=[0, 5, -1])
    _clamp_and_backfill_structure(structure, total_outlines=3, total_slide_layouts=3)
    assert structure.slides == [0, 0, 0]


def test_extra_layout_picks_are_truncated_to_outline_count():
    structure = PresentationStructureModel(slides=[0, 1, 2, 1, 0])
    _clamp_and_backfill_structure(structure, total_outlines=3, total_slide_layouts=3)
    assert structure.slides == [0, 1, 2]


def test_short_pick_list_does_not_crash():
    # Model returned fewer picks than outlines — must not IndexError (was a latent
    # crash when iterating range(total_outlines)).
    structure = PresentationStructureModel(slides=[0])
    _clamp_and_backfill_structure(structure, total_outlines=3, total_slide_layouts=3)
    assert structure.slides == [0]
