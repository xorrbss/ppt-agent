"""I1 (D21): resolve_font_embedding_default and is_cjk_language.

docs/authored-quality-speed-programme-design-20260905.md section 7: a caller
that says nothing about font_embedding should get True for a hybrid PPTX
export of a CJK-language deck, gated by EXPORT_FONT_EMBEDDING_DEFAULT_CJK. An
explicit value from the caller always wins.
"""

import pytest

from utils.export_font_embedding_default import (
    is_cjk_language,
    resolve_font_embedding_default,
)


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        (None, False),
        ("", False),
        ("Auto", False),
        ("auto", False),
        ("en", False),
        ("English", False),
        ("ko", True),
        ("ko-KR", True),
        ("KO", True),
        ("ja", True),
        ("ja-JP", True),
        ("zh", True),
        ("zh-Hans", True),
        ("zh_CN", True),
        ("Korean (한국어)", True),
        ("Japanese (日本語)", True),
        ("Chinese (Simplified - 中文, 汉语)", True),
        ("Chinese (Traditional - 中文, 漢語)", True),
        ("Spanish (Español)", False),
    ],
)
def test_is_cjk_language(language, expected):
    assert is_cjk_language(language) is expected


def test_explicit_true_and_false_always_win(monkeypatch):
    monkeypatch.delenv("EXPORT_FONT_EMBEDDING_DEFAULT_CJK", raising=False)

    assert (
        resolve_font_embedding_default(
            False, pptx_mode="hybrid", language="Korean (한국어)"
        )
        is False
    )
    assert (
        resolve_font_embedding_default(True, pptx_mode="fidelity", language="en")
        is True
    )


def test_missing_hybrid_cjk_defaults_true(monkeypatch):
    monkeypatch.delenv("EXPORT_FONT_EMBEDDING_DEFAULT_CJK", raising=False)
    assert (
        resolve_font_embedding_default(
            None, pptx_mode="hybrid", language="Korean (한국어)"
        )
        is True
    )
    assert resolve_font_embedding_default(None, pptx_mode="hybrid", language="ko") is True


def test_missing_fidelity_stays_false_even_for_cjk(monkeypatch):
    monkeypatch.delenv("EXPORT_FONT_EMBEDDING_DEFAULT_CJK", raising=False)
    assert resolve_font_embedding_default(None, pptx_mode="fidelity", language="ko") is False


def test_missing_hybrid_non_cjk_stays_false(monkeypatch):
    monkeypatch.delenv("EXPORT_FONT_EMBEDDING_DEFAULT_CJK", raising=False)
    assert resolve_font_embedding_default(None, pptx_mode="hybrid", language="en") is False


def test_knob_off_disables_the_default(monkeypatch):
    monkeypatch.setenv("EXPORT_FONT_EMBEDDING_DEFAULT_CJK", "false")
    assert resolve_font_embedding_default(None, pptx_mode="hybrid", language="ko") is False
