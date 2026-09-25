"""Line re-read jobs that share pixels on one printed row are read once."""

from __future__ import annotations

from services.source_content.raster_line_reread import _Job, _merge_row_overlaps


def _job(lines, box, scale=1.0):
    return _Job(tuple(lines), box, (box[0] - 5, box[1] - 5), scale)


def test_overlapping_jobs_on_one_row_become_one_job():
    # "시스템 화면 소기" and "! 소개 - 업무포털": two reads of the same title row.
    jobs = [_job([0], (101, 58, 355, 46), 0.7), _job([1], (338, 58, 365, 46), 0.8)]
    merged = _merge_row_overlaps(jobs)
    assert len(merged) == 1
    assert merged[0].line_indices == (0, 1)
    assert merged[0].box == (101, 58, 602, 46)
    assert merged[0].crop_origin == (96, 53)
    assert merged[0].scale == 0.7  # the first (tallest-first) job's scale


def test_separate_rows_columns_and_sizes_stay_separate_jobs():
    jobs = [
        _job([0], (100, 100, 300, 30)),
        _job([1], (420, 100, 300, 30)),  # same row, no shared pixels
        _job([2], (100, 140, 300, 30)),  # next row
        _job([3], (350, 90, 200, 60), 0.5),  # shares pixels but twice the glyph size
    ]
    assert len(_merge_row_overlaps(jobs)) == 4


def test_box_height_differences_alone_do_not_split_a_row():
    # Same glyph size (scale), but one box is taller from a descender or a stray mark.
    jobs = [_job([0], (101, 58, 339, 46), 0.71), _job([1], (353, 54, 349, 61), 0.71)]
    assert len(_merge_row_overlaps(jobs)) == 1


def test_merging_is_transitive_along_a_row():
    jobs = [_job([0], (0, 0, 100, 20)), _job([1], (90, 0, 100, 20)), _job([2], (180, 0, 100, 20))]
    merged = _merge_row_overlaps(jobs)
    assert [job.line_indices for job in merged] == [(0, 1, 2)]
