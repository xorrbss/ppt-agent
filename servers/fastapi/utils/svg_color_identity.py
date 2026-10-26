"""Pure contrast and border colors shared by SVG identity and templates."""
from __future__ import annotations


def _rgb(color: str) -> tuple[float, float, float]:
    return tuple(int(color[index:index + 2], 16) / 255 for index in (1, 3, 5))


def _luminance(color: str) -> float:
    channels = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
                for channel in _rgb(color)]
    return sum(channel * weight for channel, weight in zip(channels, (.2126, .7152, .0722), strict=False))


def _contrast(left: str, right: str) -> float:
    values = sorted((_luminance(left), _luminance(right)))
    return (values[1] + .05) / (values[0] + .05)


def contrast_foreground(background: str, preferred: str) -> str:
    if _contrast(background, preferred) >= 4.5:
        return preferred
    return max(("#FFFFFF", "#111827"), key=lambda color: _contrast(background, color))


def identity_border(background: str, foreground: str) -> str:
    return "#" + "".join(f"{round((back * .75 + front * .25) * 255):02X}"
                          for back, front in zip(_rgb(background), _rgb(foreground), strict=False))


