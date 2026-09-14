"""Read the renderer-owned canvas declaration used by capture and geometry proof."""
from __future__ import annotations

from html.parser import HTMLParser

from services.composer.plan import CANVAS_HEIGHT, CANVAS_WIDTH


class _CanvasDeclaration(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.dimensions: tuple[int, int] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "body":
            return
        values = dict(attrs)
        width = values.get("data-composer-canvas-width")
        height = values.get("data-composer-canvas-height")
        if width is None and height is None:
            return
        if self.dimensions is not None or width is None or height is None:
            raise ValueError("composer_canvas_declaration_invalid")
        try:
            dimensions = int(width), int(height)
        except (TypeError, ValueError) as exc:
            raise ValueError("composer_canvas_declaration_invalid") from exc
        if not (320 <= dimensions[0] <= 7680 and 180 <= dimensions[1] <= 4320):
            raise ValueError("composer_canvas_declaration_invalid")
        self.dimensions = dimensions


def declared_canvas_dimensions(html: str) -> tuple[int, int]:
    parser = _CanvasDeclaration()
    parser.feed(html)
    return parser.dimensions or (CANVAS_WIDTH, CANVAS_HEIGHT)
