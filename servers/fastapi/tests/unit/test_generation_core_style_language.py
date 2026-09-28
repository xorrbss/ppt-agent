"""Unit tests for services.generation_core.style.build language detection."""
from services.generation_core.style.build import _is_korean


def test_is_korean_detects_hangul_language_name():
    assert _is_korean("한국어") is True


def test_is_korean_detects_english_and_iso_forms():
    assert _is_korean("Korean") is True
    assert _is_korean(None, "ko-KR") is True


def test_is_korean_rejects_other_languages():
    assert _is_korean("English", "日本語", None) is False
