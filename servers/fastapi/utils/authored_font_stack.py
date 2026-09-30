"""CSS font stacks for authored slides: the requested family, a locally
installed fallback, then the generic family.

Pure data and string formatting, shared by the authoring prompt modules
and the infographic token renderer.
"""

from __future__ import annotations

LOCAL_FONT_FALLBACKS = {
    "noto sans kr": ("Noto Sans KR", "Malgun Gothic", "sans-serif"),
    "pretendard": ("Pretendard", "Malgun Gothic", "sans-serif"),
    "noto serif kr": ("Noto Serif KR", "Batang", "serif"),
    "inter": ("Inter", "Aptos", "sans-serif"),
    "roboto": ("Roboto", "Aptos", "sans-serif"),
    "dm sans": ("DM Sans", "Aptos", "sans-serif"),
    "source serif 4": ("Source Serif 4", "Cambria", "serif"),
    "ibm plex mono": ("IBM Plex Mono", "Consolas", "monospace"),
}


def css_font_stack(value: str) -> str:
    family = (value or "Noto Sans KR").split(",", 1)[0].strip().strip("\"'")
    logical, local, generic = LOCAL_FONT_FALLBACKS.get(
        " ".join(family.lower().split()),
        (family, "Aptos", "sans-serif"),
    )
    logical = logical.replace("\\", "\\\\").replace('"', '\\"')
    local = local.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{logical}","{local}",{generic}'
