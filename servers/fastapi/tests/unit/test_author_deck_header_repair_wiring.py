"""H2 wiring: the deterministic header repair runs before a re-author call."""

from models.authored_header_contract import default_header_contract
from utils.authored_header_validation import validate_header_contract
from utils.llm_calls import author_deck_header_repair as wiring
from utils.llm_calls.author_deck_header_repair import repair_header_violations


_ROLE = "PILLARS"
_BAND = 150


def _slide(body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'></head>"
        f"<body>{body}</body></html>"
    )


def _conforming(contract, *, band: int = _BAND) -> str:
    return _slide(
        f'<div data-header-part="band" data-header-band="{band}" '
        f'style="height:{band}px">'
        '<span data-header-part="eyebrow">대외 연계</span>'
        '<h1 data-header-part="title">단일 거래 흐름</h1>'
        '<div data-header-part="divider"></div></div>'
        f'<div data-header-part="body" '
        f'style="margin-top:{contract.content_gap_px}px">본문</div>'
    )


def _with_unknown_part(contract) -> str:
    return _conforming(contract).replace(
        '<div data-header-part="divider"></div>',
        '<div data-header-part="divider"></div>'
        '<div data-header-part="sparkle">?</div>',
    )


def test_a_repairable_violation_never_reaches_the_re_author_branch():
    contract = default_header_contract()
    html = _with_unknown_part(contract)
    violations = validate_header_contract(html, contract, role=_ROLE)
    assert violations

    receipt: list[dict] = []
    repaired, remaining = repair_header_violations(
        html,
        contract,
        role=_ROLE,
        content="본문",
        violations=violations,
        receipt=receipt,
    )

    assert remaining == ()
    assert validate_header_contract(repaired, contract, role=_ROLE) == ()
    assert receipt and receipt[0]["rule"].startswith("header_repair:")
    assert receipt[0]["count"] >= 1


def test_the_knob_off_leaves_the_candidate_and_its_violations_alone(monkeypatch):
    contract = default_header_contract()
    html = _with_unknown_part(contract)
    violations = validate_header_contract(html, contract, role=_ROLE)
    monkeypatch.setattr(
        wiring, "is_authored_header_deterministic_repair_enabled", lambda: False
    )

    receipt: list[dict] = []
    repaired, remaining = repair_header_violations(
        html,
        contract,
        role=_ROLE,
        violations=violations,
        receipt=receipt,
    )

    assert repaired == html
    assert remaining == tuple(violations)
    assert receipt == []


def test_a_conforming_slide_is_never_handed_to_the_repairer():
    contract = default_header_contract()
    html = _conforming(contract)

    repaired, remaining = repair_header_violations(
        html, contract, role=_ROLE, violations=()
    )

    assert (repaired, remaining) == (html, ())


def test_a_raising_repairer_degrades_to_the_original_candidate(monkeypatch):
    contract = default_header_contract()
    html = _with_unknown_part(contract)
    violations = validate_header_contract(html, contract, role=_ROLE)

    import utils.authored_header_repair as repair_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("repair exploded")

    monkeypatch.setattr(repair_mod, "repair_header_contract", boom)

    repaired, remaining = repair_header_violations(
        html, contract, role=_ROLE, violations=violations
    )

    assert repaired == html
    assert remaining == tuple(violations)


# --- the eyebrow-prefix violation, which the wiring must route to a second
# repair module rather than to a re-author call -----------------------------

_PREFIX = "Ⅱ. 제안개요 > "


def _breadcrumb_slide(eyebrow_text: str, contract, *, band: int = _BAND) -> str:
    return _slide(
        f'<div data-header-part="band" data-header-band="{band}" '
        f'style="height:{band}px">'
        f'<span data-header-part="eyebrow">{eyebrow_text}</span>'
        '<h1 data-header-part="title">단일 거래 흐름</h1>'
        '<div data-header-part="divider"></div></div>'
        f'<div data-header-part="body" '
        f'style="margin-top:{contract.content_gap_px}px">본문</div>'
    )


def test_a_dropped_chapter_prefix_is_repaired_without_a_re_author_call():
    contract = default_header_contract(chapter_breadcrumb=True)
    html = _breadcrumb_slide("목표 아키텍처", contract)
    violations = validate_header_contract(
        html, contract, role=_ROLE, content="본문", chapter_prefix=_PREFIX
    )
    assert "header_eyebrow_prefix_missing" in violations

    receipt: list[dict] = []
    repaired, remaining = repair_header_violations(
        html,
        contract,
        role=_ROLE,
        content="본문",
        violations=violations,
        receipt=receipt,
        chapter_prefix=_PREFIX,
    )

    assert "header_eyebrow_prefix_missing" not in remaining
    assert (
        validate_header_contract(
            repaired, contract, role=_ROLE, content="본문", chapter_prefix=_PREFIX
        )
        == ()
    )
    assert any("header_eyebrow_prefix_missing" in r["rule"] for r in receipt)


def test_without_a_chapter_prefix_the_violation_is_never_even_raised():
    """The call sites only compute the eyebrow-prefix violation when the deck
    has a chapter plan; without one, ``chapter_prefix`` stays ``None`` and
    the wiring has nothing to repair."""
    contract = default_header_contract(chapter_breadcrumb=True)
    html = _breadcrumb_slide("목표 아키텍처", contract)
    violations = validate_header_contract(html, contract, role=_ROLE, content="본문")

    assert "header_eyebrow_prefix_missing" not in violations

    repaired, remaining = repair_header_violations(
        html, contract, role=_ROLE, content="본문", violations=violations
    )

    assert (repaired, remaining) == (html, ())


def test_an_unresolvable_eyebrow_prefix_violation_falls_through_to_re_authoring(
    monkeypatch,
):
    """When the eyebrow repair cannot help (it raises, or -- equivalently for
    the caller -- refuses because the eyebrow is not locatable), the
    violation must survive into ``remaining`` for the caller to re-author,
    exactly like a raising ``repair_header_contract`` degrades to the
    original candidate above."""
    contract = default_header_contract(chapter_breadcrumb=True)
    html = _breadcrumb_slide("목표 아키텍처", contract)
    violations = validate_header_contract(
        html, contract, role=_ROLE, content="본문", chapter_prefix=_PREFIX
    )
    assert "header_eyebrow_prefix_missing" in violations

    import utils.authored_header_eyebrow_repair as eyebrow_repair_mod

    def boom(*_args, **_kwargs):
        raise RuntimeError("eyebrow repair exploded")

    monkeypatch.setattr(eyebrow_repair_mod, "repair_eyebrow_prefix", boom)

    repaired, remaining = repair_header_violations(
        html,
        contract,
        role=_ROLE,
        content="본문",
        violations=violations,
        chapter_prefix=_PREFIX,
    )

    assert repaired == html
    assert remaining == tuple(violations)
