"""The LIVE-4 R30 deck: 28 of 30 pages reviewed, pages 25 and 29 (zero-based) unreviewed.

Its publication raised IndexError at publish.py:247 -> svg_export_visual_review.py:95 on 481e07035, because the
compact 28-entry reviewed-digest list was indexed by deck position. The fixture holds only indices, flags and
digests of that deck's retained checkpoint advisory; no slide text or render bytes. The full producer ->
publication -> export pass over the same topology is the 30-page case of
``test_svg_subset_publication.test_subset_publication_and_export_keep_original_page_identity``.
"""
from types import SimpleNamespace

from services.generation_core.quality.review_coverage import review_coverage
from services.svg_export_visual_review import split_page_advisories
from tests.unit.test_svg_subset_publication import LIVE4_FIXTURE as LIVE4, subset_review_deck
from utils.hashing import sha256_hex

PAGES, EXCLUDED = LIVE4["pageCount"], LIVE4["unreviewedPages"]
REVIEWED = [index for index in range(PAGES) if index not in EXCLUDED]


def _retained_vision():
    return dict(slideCount=PAGES, slides=[], reviewedPageIndices=list(LIVE4["reviewedPageIndices"]),
                reviewedPngDigests=list(LIVE4["reviewedPngDigests"]), unreviewedPages=list(EXCLUDED),
                reason=LIVE4["reason"])


def test_fixture_is_the_live_topology():
    assert (PAGES, EXCLUDED) == (30, [25, 29])
    assert LIVE4["reviewedPageIndices"] == REVIEWED and len(LIVE4["reviewedPngDigests"]) == 28
    assert LIVE4["reviewedPngDigests"] == [LIVE4["pagePngDigests"][index] for index in REVIEWED]
    assert LIVE4["gate"]["status"] == "unreviewed" and LIVE4["gate"]["pageCoverageComplete"] is False
    assert LIVE4["gate"]["flowUnreviewedPages"] == EXCLUDED and LIVE4["deckFlowEvaluated"] is False


def test_the_producer_reproduces_the_live_review_shape(monkeypatch):
    _, advisory, _ = subset_review_deck(monkeypatch, PAGES, EXCLUDED)
    vision = advisory["vision"]
    gate = vision["designGate"]
    assert vision["reviewedPageIndices"] == LIVE4["reviewedPageIndices"]
    assert len(vision["reviewedPngDigests"]) == len(LIVE4["reviewedPngDigests"])
    assert vision["unreviewedPages"] == LIVE4["unreviewedPages"] and vision["reason"] == LIVE4["reason"]
    assert gate["status"] == LIVE4["gate"]["status"] and gate["pageCoverageComplete"] is False
    assert gate["unreviewedPages"] == LIVE4["gate"]["unreviewedPages"]
    assert gate["flowUnreviewedPages"] == LIVE4["gate"]["flowUnreviewedPages"]
    assert vision["designReview"]["deckFlow"]["evaluated"] is LIVE4["deckFlowEvaluated"] is False


def test_coverage_reads_the_live_digests_by_page_identity():
    coverage = review_coverage(_retained_vision())
    assert coverage.valid and coverage.indices == REVIEWED
    assert coverage.unreviewed(range(PAGES)) == EXCLUDED
    assert [coverage.digest(index) for index in EXCLUDED] == [None, None]
    own = LIVE4["pagePngDigests"]
    assert all(coverage.digest(index) == own[index] for index in REVIEWED)
    # The neighbour a positional read would have handed the excluded pages.
    assert coverage.digest(25) not in (own[24], own[26]) and coverage.digest(29) not in (own[28],)
    assert coverage.matches(REVIEWED, LIVE4["reviewedPngDigests"])
    assert coverage.projection(range(PAGES)) == dict(reviewedPages=REVIEWED, unreviewedPages=EXCLUDED, totalPages=PAGES)


def test_page_records_bind_each_page_to_its_own_digest_and_mark_the_excluded_pages():
    slides = [SimpleNamespace(svg_content=f"<svg>page {index}</svg>",
                              content={"svgReceipt": {"png_sha256": LIVE4["pagePngDigests"][index]}})
              for index in range(PAGES)]
    records = split_page_advisories(_retained_vision(), slides, range(PAGES))
    assert [record["index"] for record in records] == list(range(PAGES))
    for record in records:
        index, page = record["index"], record["vision"]
        assert record["pngDigest"] == LIVE4["pagePngDigests"][index]
        assert record["pageDigest"] == sha256_hex(slides[index].svg_content.encode())
        if index in EXCLUDED:
            assert (page["reviewedPageIndices"], page["reviewedPngDigests"]) == ([], [])
            assert page["skipped"] == "page_not_reviewed" and page["unreviewedPages"] == [index]
            assert page["slides"] == []
        else:
            assert page["reviewedPageIndices"] == [index]
            assert page["reviewedPngDigests"] == [LIVE4["pagePngDigests"][index]]
            assert "skipped" not in page
