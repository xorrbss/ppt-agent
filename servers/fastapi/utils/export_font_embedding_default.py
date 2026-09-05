"""I1 (docs/authored-quality-speed-programme-design-20260905.md section 7,
D21): default ``font_embedding`` to ``True`` for hybrid PPTX exports of
CJK-language decks when the caller said nothing.

Every ``font_embedding`` call site used to collapse "the caller did not send
this field" and "the caller explicitly said false" into the same Python
``False``. Without embedding, ``font-policy.ts`` (frontend) requires
embedding for every CJK run and fails closed, so every Hangul/Kana/Han run
is exported as a raster picture unless the caller happened to opt in. This
module gives call sites a real tri-state: pass ``requested=None`` when the
field was absent from the request body / command context, ``True``/``False``
when the caller stated it. An explicit ``False`` always wins over the
default.
"""

from __future__ import annotations

from utils.get_env_programme import is_export_font_embedding_default_cjk_enabled
from utils.runtime_settings import RuntimeSettings

#: Primary BCP-47 subtags for Korean, Japanese, Chinese.
_CJK_LANGUAGE_PRIMARY_TAGS = ("ko", "ja", "zh")

#: ``PresentationModel.language`` is whatever the request sent; this fork's
#: upload flow sends a display string ("Korean (한국어)", "Chinese
#: (Simplified - 中文, 汉语)") rather than a language tag, so both forms are
#: matched by prefix.
_CJK_LANGUAGE_NAME_PREFIXES = ("korean", "japanese", "chinese")


def is_cjk_language(code: str | None) -> bool:
    """Whether a stored presentation language names Korean, Japanese, or Chinese.

    Matches both a short language tag ("ko", "ko-KR", "zh-Hans") and this
    fork's upload-flow display string. An unset value or the literal "Auto"
    (no language pinned yet) is not CJK: there is no source to classify, so
    the default stays conservative.
    """
    if not code:
        return False
    normalized = code.strip().lower()
    if not normalized or normalized == "auto":
        return False
    primary_tag = normalized.replace("_", "-").split("-", 1)[0]
    if primary_tag in _CJK_LANGUAGE_PRIMARY_TAGS:
        return True
    return normalized.startswith(_CJK_LANGUAGE_NAME_PREFIXES)


def resolve_font_embedding_default(
    requested: bool | None,
    *,
    pptx_mode: str,
    language: str | None,
    settings: RuntimeSettings | None = None,
) -> bool:
    """Resolve the effective ``font_embedding`` flag for one export.

    - ``requested`` not ``None`` (the caller stated it, ``True`` or
      ``False``) always wins and is returned unchanged.
    - ``requested is None`` (the caller said nothing) becomes ``True`` only
      when the export is hybrid PPTX, the deck language is CJK, and
      ``EXPORT_FONT_EMBEDDING_DEFAULT_CJK`` is on; otherwise ``False``,
      matching the behaviour every call site had before this default existed.
    """
    if requested is not None:
        return requested
    if pptx_mode != "hybrid":
        return False
    if not is_cjk_language(language):
        return False
    return is_export_font_embedding_default_cjk_enabled(settings)
