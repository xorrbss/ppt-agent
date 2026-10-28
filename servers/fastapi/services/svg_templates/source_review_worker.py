"""Disposable source-review PowerPoint owner process."""
from __future__ import annotations
import base64
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def main():
    try:
        from models.document_canvas import DocumentCanvasMetadata
        from models.svg_template_frame import SvgTemplateFrame
        from services.svg_templates.source_review import _render_source
        request = json.load(sys.stdin)
        frame = SvgTemplateFrame.model_validate(request["frame"])
        package = SimpleNamespace(source_digest=request["source_digest"],
            source_canvas=DocumentCanvasMetadata.model_validate(request["canvas"]),
            font_manifest=frame.font_manifest, frames=(frame,))
        version, images = _render_source(Path(request["path"]), package,
                                         ownership_path=request["ownership_path"])
        result = {"status": "ok", "version": version,
                  "png": base64.b64encode(images[frame.source_page]).decode("ascii")}
    except Exception as error:
        safe = {"svg_template_powerpoint_unavailable", "svg_template_powerpoint_fonts_unavailable", "svg_template_powerpoint_busy",
                "svg_template_powerpoint_ownership_unproven", "svg_template_source_digest_mismatch",
                "svg_template_source_page_missing"}
        result = {"status": "blocked", "reason": str(error) if str(error) in safe
                  else "svg_template_review_render_unavailable"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
