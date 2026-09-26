"""Shared source discriminator validation for rows and history snapshots."""


def validate_slide_source(source_format, html_content, svg_content, ui):
    present = {
        kind
        for kind, exists in (("html", bool(html_content)), ("svg", bool(svg_content)), ("native", ui is not None))
        if exists
    }
    if len(present) > 1:
        raise ValueError("slide_source_payloads_cannot_coexist")
    if source_format is not None and source_format not in {"html", "svg", "native"}:
        raise ValueError("unsupported_slide_source_format")
    if source_format is not None and present and source_format not in present:
        raise ValueError("slide_source_format_conflict")
