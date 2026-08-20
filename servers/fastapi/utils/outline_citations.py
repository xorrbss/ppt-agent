"""Outline text may carry citations; slide text must not carry their URLs.

With web search on, the outline model cites its sources inline, as a markdown
link. The authored pipeline hands that outline text to the slide author
verbatim, so the author sees a link and writes one: an `<a href>`, an element
the renderer isolation policy refuses to admit. The slide fails, the retry
re-sends the same prompt and fails the same way, and strict authoring refuses a
placeholder — so three linked slides out of eight failed a whole deck.

Removing the URL removes the reason the author reaches for a link at all, and
is not a loss: the visible label is what made the citation readable, and it
survives. It also returns the bullet to its length budget. In the deck that
prompted this, one bullet ran 149 characters against a 24-character concise
ceiling, and nearly all of the excess was a tracking-parameterized URL.
"""

import re
from urllib.parse import urlsplit

from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)

# `[label](url)`, tolerating a bracketed URL and a markdown title, and the `!`
# of an image so an embedded image collapses to its alt text rather than to
# `!alt`.
_MARKDOWN_LINK = re.compile(
    r"!?\[([^\]\n]*)\]\(\s*<?(https?://[^\s)>]+)>?(?:\s+\"[^\"]*\")?\s*\)"
)
# `<https://example.com>` — an autolink, and also a token the HTML validators
# downstream would read as an unknown tag.
_ANGLE_AUTOLINK = re.compile(r"<(https?://[^\s>]+)>")
_BARE_URL = re.compile(r"https?://[^\s<>)\]\"']+")
_EMPTY_WRAPPER = re.compile(r"\(\s*\)|\[\s*\]")
_SPACE_INSIDE_PARENS = re.compile(r"\(\s+|\s+\)")
_REPEATED_SPACES = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([,.;:)\]])")
_TRAILING_SPACES = re.compile(r"[ \t]+$", re.MULTILINE)


def _host(url: str) -> str:
    """The site a URL points at, as a reader would name it."""
    host = urlsplit(url).netloc.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _label_for_link(match: re.Match) -> str:
    """A markdown link keeps its label; an unlabeled one falls back to the host,
    which is what these citations use for a label anyway."""
    label = match.group(1).strip()
    return label or _host(match.group(2))


def _tidy(text: str) -> str:
    """Close the gaps a removed URL leaves behind, without reflowing the text:
    only whitespace and now-empty brackets are touched, and lines stay put."""
    text = _EMPTY_WRAPPER.sub("", text)
    text = _SPACE_INSIDE_PARENS.sub(lambda m: m.group(0).strip(), text)
    text = _REPEATED_SPACES.sub(" ", text)
    text = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)
    return _TRAILING_SPACES.sub("", text)


def strip_outline_citation_links(text: str) -> str:
    """Return `text` with every URL replaced by what it was citing.

    A labelled link becomes its label, and a bare URL becomes its host, so the
    attribution survives and only the address is gone. Text without a URL is
    returned unchanged — including its whitespace, which nothing here rewrites
    unless a URL was actually removed.
    """
    if not text:
        return text
    stripped = _MARKDOWN_LINK.sub(_label_for_link, text)
    stripped = _ANGLE_AUTOLINK.sub(lambda m: _host(m.group(1)), stripped)
    stripped = _BARE_URL.sub(lambda m: _host(m.group(0)), stripped)
    return _tidy(stripped) if stripped != text else text


def strip_outline_citations(
    outline: PresentationOutlineModel,
) -> PresentationOutlineModel:
    """The same removal across a whole outline, as a new model."""
    return PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content=strip_outline_citation_links(slide.content))
            for slide in outline.slides
        ]
    )
