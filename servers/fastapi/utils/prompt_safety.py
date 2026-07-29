import html
import re
from typing import Optional


_UNTRUSTED_SOURCE_DOCUMENT_TAG_PATTERN = re.compile(
    r"</?untrusted_source_documents(?:\s[^>]*)?>", re.IGNORECASE
)


def format_untrusted_source_documents(additional_context: Optional[str]) -> str:
    """Wrap source text without allowing it to close its prompt-data boundary."""
    if not additional_context:
        return "None"

    escaped_context = _UNTRUSTED_SOURCE_DOCUMENT_TAG_PATTERN.sub(
        lambda match: html.escape(match.group(0), quote=False), additional_context
    )
    return (
        "<untrusted_source_documents>\n"
        f"{escaped_context}\n"
        "</untrusted_source_documents>"
    )
