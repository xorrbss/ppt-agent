"""Local, artifact-only export probe for reviewed A4 SVG reconstruction pages.

This pilot deliberately does not publish a presentation or call a provider. The
conversion work runs in a child process using the existing SVG worker functions.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
FASTAPI_ROOT = ROOT / "servers" / "fastapi"
SVG_NS = "{http://www.w3.org/2000/svg}"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
EXPECTED_PAGES = (3, 8, 11, 22, 24)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pages", type=int, nargs="+", default=list(EXPECTED_PAGES))
    parser.add_argument("--orientation", choices=("landscape", "portrait"), default="landscape")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def _pixel_identity(image_bytes: bytes) -> str:
    from PIL import Image

    with Image.open(BytesIO(image_bytes)) as image:
        rgba = image.convert("RGBA")
        digest = hashlib.sha256(rgba.tobytes()).hexdigest()
        return f"{rgba.width}x{rgba.height}:{digest}"


def _text_characters(value: str) -> Counter[str]:
    return Counter(char for char in unicodedata.normalize("NFC", value) if not char.isspace())


def _load_svg(path: Path, width: int, height: int) -> tuple[str, Counter[str], Counter[str], int]:
    source = path.read_text(encoding="utf-8")
    root = ET.fromstring(source)
    if root.tag != f"{SVG_NS}svg":
        raise ValueError(f"{path.name}: invalid SVG root")
    expected_view_box = (0.0, 0.0, float(width), float(height))
    view_box = tuple(float(v) for v in re.split(r"[\s,]+", root.attrib.get("viewBox", "").strip()) if v)
    if view_box != expected_view_box:
        raise ValueError(f"{path.name}: canvas viewBox mismatch")
    for attribute, expected in (("width", width), ("height", height)):
        actual = root.attrib.get(attribute, "").removesuffix("px")
        if not actual or float(actual) != expected:
            raise ValueError(f"{path.name}: canvas {attribute} mismatch")

    source_text: Counter[str] = Counter()
    source_images: Counter[str] = Counter()
    distorted_images = 0
    for element in root.iter():
        local_name = element.tag.rsplit("}", 1)[-1]
        if local_name in {"script", "foreignObject", "iframe"}:
            raise ValueError(f"{path.name}: active SVG content unsupported")
        if local_name == "text":
            source_text.update(_text_characters("".join(element.itertext())))
        if local_name != "image":
            continue
        href = element.attrib.get("href") or element.attrib.get(XLINK_HREF, "")
        if not href.startswith("data:image/png;base64,"):
            raise ValueError(f"{path.name}: screenshot image must be embedded PNG")
        try:
            from PIL import Image

            payload = base64.b64decode(href.split(",", 1)[1], validate=True)
            with Image.open(BytesIO(payload)) as screenshot:
                image_width, image_height = screenshot.size
            source_images[_pixel_identity(payload)] += 1
            box_width = float(element.attrib.get("width", "0").removesuffix("px"))
            box_height = float(element.attrib.get("height", "0").removesuffix("px"))
            if box_width <= 0 or box_height <= 0:
                raise ValueError("image bounds must be positive")
            fit = element.attrib.get("preserveAspectRatio", "xMidYMid meet").strip()
            if fit == "none" and abs(box_width / box_height - image_width / image_height) > 0.001:
                distorted_images += 1
        except (ValueError, OSError) as exc:
            raise ValueError(f"{path.name}: invalid embedded screenshot PNG") from exc
    if not source_text or not source_images:
        raise ValueError(f"{path.name}: text and screenshot images are both required")
    return source, source_text, source_images, distorted_images


def _slide_shapes(slide):
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    pending = list(slide.shapes)
    while pending:
        shape = pending.pop(0)
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            pending[0:0] = list(shape.shapes)
        else:
            yield shape


def _inspect_pptx(
    path: Path, expected_text: list[Counter[str]], expected_images: list[Counter[str]], canvas
) -> list[dict]:
    from pptx import Presentation

    deck = Presentation(str(path))
    if len(deck.slides) != len(expected_text):
        raise ValueError("PPTX slide count differs from source SVG count")
    if (deck.slide_width, deck.slide_height) != (canvas.physical_size.width_emu, canvas.physical_size.height_emu):
        raise ValueError("PPTX physical page size differs from A4 canvas")
    pages = []
    for slide, text_expected, images_expected in zip(deck.slides, expected_text, expected_images, strict=False):
        text_actual: Counter[str] = Counter()
        images_actual: Counter[str] = Counter()
        text_shapes = native_tables = native_shapes = 0
        for shape in _slide_shapes(slide):
            native_shapes += 1
            if shape.has_table:
                native_tables += 1
                for row in shape.table.rows:
                    for cell in row.cells:
                        text_actual.update(_text_characters(cell.text))
            elif shape.has_text_frame:
                value = shape.text_frame.text
                if value.strip():
                    text_shapes += 1
                    text_actual.update(_text_characters(value))
            if shape.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                images_actual[_pixel_identity(shape.image.blob)] += 1
        missing_text = sum((text_expected - text_actual).values())
        extra_text = sum((text_actual - text_expected).values())
        missing_images = sum((images_expected - images_actual).values())
        if missing_text or extra_text or missing_images:
            raise ValueError(
                "PPTX readback differs from source SVG: "
                f"missing_characters={missing_text}, extra_characters={extra_text}, "
                f"missing_screenshot_images={missing_images}"
            )
        pages.append(
            {
                "editableTextShapes": text_shapes,
                "nativeShapes": native_shapes,
                "nativeTables": native_tables,
                "embeddedScreenshotImages": sum(images_expected.values()),
                "textCharactersMatched": sum(text_expected.values()),
                "textCharacterMultisetMatched": True,
                "textRunOrderVerified": False,
                "sourcePixelsMatched": True,
            }
        )
    return pages


if __package__:
    from .raster_pilot_pdf import _admit_pdf_sources, _inspect_pdf, _make_pdf
else:
    from raster_pilot_pdf import _admit_pdf_sources, _inspect_pdf, _make_pdf


def _work(args: argparse.Namespace) -> dict:
    sys.path.insert(0, str(FASTAPI_ROOT))
    from services.document_canvas import document_canvas_for_orientation
    from workers.public_svg_export_worker import convert_pptx

    canvas = document_canvas_for_orientation(args.orientation)
    pages = list(args.pages)
    if not pages or len(set(pages)) != len(pages):
        raise ValueError("pages must be unique and in source order")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = [args.input_dir / f"page-{page:03d}.svg" for page in pages]
    sources = []
    expected_text = []
    expected_images = []
    distorted_images = []
    for path in paths:
        source, text, images, distorted = _load_svg(path, canvas.logical_size.width, canvas.logical_size.height)
        sources.append(source)
        expected_text.append(text)
        expected_images.append(images)
        distorted_images.append(distorted)

    pptx_path = args.output_dir / "raster-reconstruction-pilot.pptx"
    pdf_path = args.output_dir / "raster-reconstruction-pilot.pdf"
    converted = convert_pptx(paths, pptx_path, [""] * len(paths), canvas)
    if not converted:
        raise ValueError("native PPTX conversion did not complete")
    readback = _inspect_pptx(pptx_path, expected_text, expected_images, canvas)
    for page_result, distorted in zip(readback, distorted_images, strict=False):
        page_result["distortedScreenshotImages"] = distorted
        page_result["screenshotRenderAspectVerified"] = distorted == 0
    summary = {
        "status": "export_checks_blocked",
        "pages": pages,
        "orientation": args.orientation,
        "logicalSize": [canvas.logical_size.width, canvas.logical_size.height],
        "physicalSizeEmu": [canvas.physical_size.width_emu, canvas.physical_size.height_emu],
        "pptxPath": str(pptx_path),
        "pptxChecksPassed": True,
        "pptxConversionBoundary": "local-child-native-converter",
        "productWorkerAdmissionVerified": False,
        "localAssetTransportFixture": True,
        "ownerAuthorizationVerified": False,
        "databaseCommittedArtifactVerified": False,
        "pdfPath": None,
        "pdfStatus": "pending_admission",
        "pdfAdmissionBlockers": [],
        "svgLineage": [],
        "pdfChecksPassed": False,
        "pdfEditable": False,
        "pdfEncoding": "page-image",
        "pdfContentQualityVerified": False,
        "visualQualityVerified": False,
        "sourceContentVerified": False,
        "completeProduct": False,
        "textRunOrderVerified": False,
        "screenshotRenderAspectVerified": not any(distorted_images),
        "pptxReadback": readback,
    }
    summary_path = args.output_dir / "export-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    original_sha256 = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    try:
        admitted, blockers, lineage = _admit_pdf_sources(
            sources,
            pages,
            original_sha256,
            canvas,
            args.output_dir,
        )
    except Exception as exc:
        message = str(exc)
        token = message.split(":", 1)[0]
        code = token if re.fullmatch(r"(?:svg|public_worker)_[a-z0-9_]+", token) else type(exc).__name__
        summary["pdfStatus"] = "blocked_fixture"
        summary["pdfAdmissionBlockers"] = [{"code": code}]
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    summary["svgLineage"] = lineage
    if blockers:
        summary["pdfStatus"] = "blocked_admission"
        summary["pdfAdmissionBlockers"] = blockers
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    try:
        _make_pdf(admitted, pdf_path, canvas)
        _inspect_pdf(pdf_path, len(paths), canvas)
    except Exception as exc:
        summary["pdfStatus"] = "blocked_runtime"
        summary["pdfAdmissionBlockers"] = [{"code": type(exc).__name__}]
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
    summary["pdfPath"] = str(pdf_path)
    summary["pdfStatus"] = "page_size_count_passed"
    summary["pdfChecksPassed"] = True
    summary["status"] = "export_checks_failed" if any(distorted_images) else "export_checks_passed"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    args = _arguments()
    if args.worker:
        try:
            _work(args)
        except Exception as exc:
            print(f"pilot-export-error:{type(exc).__name__}", file=sys.stderr)
            return 1
        return 0

    interpreter = FASTAPI_ROOT / ".venv" / "Scripts" / "python.exe"
    if not interpreter.exists():
        raise SystemExit("FastAPI virtual environment is unavailable")
    command = [
        str(interpreter),
        str(Path(__file__).resolve()),
        "--worker",
        "--input-dir",
        str(args.input_dir.resolve()),
        "--output-dir",
        str(args.output_dir.resolve()),
        "--orientation",
        args.orientation,
        "--pages",
        *(str(page) for page in args.pages),
    ]
    environment = os.environ.copy()
    environment.setdefault("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", str(512 * 1024 * 1024))
    environment.setdefault("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "900")
    environment.setdefault("PPT_WORKER_OUTPUT_LIMIT_BYTES", str(64 * 1024 * 1024))
    try:
        result = subprocess.run(
            command,
            cwd=FASTAPI_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("pilot export worker timed out") from exc
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
        raise SystemExit(f"pilot export worker failed: {detail[:220]}")
    summary = json.loads((args.output_dir / "export-summary.json").read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": summary["status"],
                "pages": summary["pages"],
                "pptxChecksPassed": summary["pptxChecksPassed"],
                "pdfStatus": summary["pdfStatus"],
                "pdfAdmissionBlockers": summary["pdfAdmissionBlockers"],
                "pptxEditableTextShapes": sum(page["editableTextShapes"] for page in summary["pptxReadback"]),
                "pptxNativeTables": sum(page["nativeTables"] for page in summary["pptxReadback"]),
                "screenshotImagesMatched": sum(page["embeddedScreenshotImages"] for page in summary["pptxReadback"]),
                "distortedScreenshotImages": sum(page["distortedScreenshotImages"] for page in summary["pptxReadback"]),
                "pdfEncoding": summary["pdfEncoding"],
                "summaryPath": str(args.output_dir / "export-summary.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["status"] == "export_checks_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
