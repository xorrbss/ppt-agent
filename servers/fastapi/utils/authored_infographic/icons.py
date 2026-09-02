"""The built-in stroke icon set a figure's ``.ig-ic`` slot may carry.

Every icon is a 24x24 stroke drawing with no fill, no text and no attributes
beyond the namespace and viewBox the renderer already admits, so the library
stylesheet can colour it through ``currentColor``. The set is deliberately
small: it is a placeholder until the icon pack stage supplies generated
cutouts, and the contract tells the model to pick from it by name.
"""

from __future__ import annotations

_NS = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"'

ICON_PATHS: dict[str, tuple[str, str]] = {
    "pulse": ("센서·감지·신호", '<path d="M2 12h4l3 8 4-16 3 8h6"/>'),
    "chart": (
        "추세·예측·성장",
        '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/><path d="M15.5 8H19v3.5"/>',
    ),
    "clipboard": (
        "점검·조치·과업",
        '<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8.5 5V3h7v2"/>'
        '<path d="M8.5 13l2.5 2.5 4.5-4.5"/>',
    ),
    "cpu": (
        "모델·데이터·시스템",
        '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/>'
        '<path d="M9 2v3M15 2v3M9 19v3M15 19v3M19 9h3M19 15h3M2 9h3M2 15h3"/>',
    ),
    "loop": (
        "순환·반복·학습",
        '<path d="M21 4v6h-6"/><path d="M3 20v-6h6"/><path d="M4.6 9a8 8 0 0113.1-3L21 10"/>'
        '<path d="M19.4 15a8 8 0 01-13.1 3L3 14"/>',
    ),
    "gateway": (
        "수집·장비·연동",
        '<rect x="3" y="13" width="18" height="8" rx="2"/><path d="M7 17h.01M11 17h6"/>'
        '<path d="M12 10V4M8.5 7.5L12 4l3.5 3.5"/>',
    ),
    "shield": (
        "보안·안정·보호",
        '<path d="M12 2l8 3v6c0 5-3.4 9.2-8 11-4.6-1.8-8-6-8-11V5l8-3z"/><path d="M8.5 12l2.5 2.5 4.5-4.5"/>',
    ),
    "warning": ("리스크·경고·이슈", '<path d="M12 3L2.5 20h19L12 3z"/><path d="M12 10v4M12 17.5v.5"/>'),
    "people": (
        "인력·조직·고객",
        '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0113 0"/>'
        '<path d="M16 4.5a3.5 3.5 0 010 7"/><path d="M17.5 14.5a6 6 0 014 5.5"/>',
    ),
    "calendar": (
        "일정·기간·마일스톤",
        '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    ),
    "gear": (
        "설정·공정·설비",
        '<circle cx="12" cy="12" r="3"/>'
        '<path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    ),
    "target": (
        "목표·성과·핵심",
        '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>',
    ),
    "coins": (
        "비용·투자·매출",
        '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/>'
        '<path d="M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    ),
    "flag": ("단계·이정표·완료", '<path d="M5 21V4"/><path d="M5 4h12l-2.5 4L17 12H5"/>'),
    "box": (
        "재고·자재·제품",
        '<path d="M3 7l9-4 9 4v10l-9 4-9-4V7z"/><path d="M3 7l9 4 9-4M12 11v10"/>',
    ),
    "arrows": (
        "전환·비교·교환",
        '<path d="M4 8h13l-3-3M20 16H7l3 3"/>',
    ),
}

ICON_NAMES: tuple[str, ...] = tuple(ICON_PATHS)


def icon_svg(name: str) -> str:
    """The inline SVG for a named icon; unknown names fall back to ``target``."""
    _, body = ICON_PATHS.get(name, ICON_PATHS["target"])
    return f"<svg {_NS}>{body}</svg>"


def icon_reference_lines() -> str:
    """The icon menu shown to the model: name, meaning and the exact SVG."""
    return "\n".join(
        f"- `{name}` ({meaning}): `{icon_svg(name)}`"
        for name, (meaning, _) in ICON_PATHS.items()
    )


__all__ = ["ICON_NAMES", "ICON_PATHS", "icon_reference_lines", "icon_svg"]
