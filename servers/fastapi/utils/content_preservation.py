"""Internal marker for content-preserving template conversions.

The marker is stored with a derived presentation's instructions so subsequent
regeneration keeps the same no-invention contract.  Prompt builders remove the
marker before exposing the remaining user instructions to the model.
"""

from typing import Optional


CONTENT_PRESERVATION_MARKER = "[[CONTENT_PRESERVING_TEMPLATE_CONVERSION]]"


def with_content_preservation(instructions: Optional[str]) -> str:
    """Return instructions marked for source-faithful schema projection."""
    clean = (instructions or "").strip()
    if CONTENT_PRESERVATION_MARKER in clean:
        return clean
    return "\n\n".join(part for part in (clean, CONTENT_PRESERVATION_MARKER) if part)


def requires_content_preservation(instructions: Optional[str]) -> bool:
    return bool(instructions and CONTENT_PRESERVATION_MARKER in instructions)


def without_content_preservation_marker(instructions: Optional[str]) -> Optional[str]:
    if not instructions:
        return instructions
    clean = instructions.replace(CONTENT_PRESERVATION_MARKER, "").strip()
    return clean or None
