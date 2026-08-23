"""Extract same-attempt browser PNG evidence from Authored export PPTX files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import zipfile
from xml.etree import ElementTree
from io import BytesIO

from PIL import Image


REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def write_fresh(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def slide_image(zip_file: zipfile.ZipFile, slide_number: int) -> tuple[str, bytes]:
    slide_name = f"ppt/slides/slide{slide_number}.xml"
    rel_name = f"ppt/slides/_rels/slide{slide_number}.xml.rels"
    slide = ElementTree.fromstring(zip_file.read(slide_name))
    rels = ElementTree.fromstring(zip_file.read(rel_name))
    targets = {
        node.attrib["Id"]: node.attrib["Target"]
        for node in rels.findall(f"{{{REL_NS}}}Relationship")
        if node.attrib.get("Type", "").endswith("/image")
    }
    candidates: list[tuple[int, str, bytes]] = []
    for blip in slide.findall(f".//{{{DRAWING_NS}}}blip"):
        rel_id = blip.attrib.get(f"{{{OFFICE_REL_NS}}}embed")
        target = targets.get(rel_id or "")
        if target:
            media_name = str((Path("ppt/slides") / target).resolve()).replace("\\", "/")
            match = re.search(r"(?:^|/)ppt/media/[^/]+$", media_name)
            if not match:
                raise RuntimeError(f"browser-source-image-target-invalid:{target}")
            archive_name = media_name[match.start() + (1 if media_name[match.start()] == "/" else 0) :]
            content = zip_file.read(archive_name)
            with Image.open(BytesIO(content)) as image:
                candidates.append((image.width * image.height, archive_name, content))
    if not candidates:
        raise RuntimeError(f"browser-source-image-missing:slide-{slide_number:02d}")
    _, archive_name, content = max(candidates)
    return archive_name, content


def extract(pptx: Path, output: Path, *, resize: bool) -> list[dict[str, object]]:
    output.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, object]] = []
    with zipfile.ZipFile(pptx) as archive:
        for slide_number in range(1, 9):
            archive_name, content = slide_image(archive, slide_number)
            raw_path = output / f"slide-{slide_number:02d}.png"
            raw_path.write_bytes(content)
            with Image.open(raw_path) as image:
                source_size = [image.width, image.height]
                if image.format != "PNG":
                    raise RuntimeError(f"browser-source-image-not-png:{archive_name}")
                if resize:
                    resized = image.convert("RGBA").resize((1280, 720), Image.Resampling.LANCZOS)
                    resized.save(raw_path, format="PNG", optimize=False)
                elif image.size != (1280, 720):
                    raise RuntimeError(f"fidelity-browser-source-size-invalid:{image.size}")
            records.append(
                {
                    "slideNumber": slide_number,
                    "archiveName": archive_name,
                    "sourceDimensions": source_size,
                    "outputDimensions": [1280, 720],
                    "outputSha256": digest(raw_path),
                }
            )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fidelity-pptx", required=True, type=Path)
    parser.add_argument("--hybrid-browser-pptx", required=True, type=Path)
    parser.add_argument("--fidelity-export-attempt-id", required=True)
    parser.add_argument("--hybrid-export-attempt-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise RuntimeError("browser-source-output-must-be-fresh")
    args.output_dir.mkdir(parents=True)
    hybrid_copy = args.output_dir / "hybrid-browser-source.pptx"
    shutil.copyfile(args.hybrid_browser_pptx.resolve(), hybrid_copy)
    fidelity = extract(args.fidelity_pptx.resolve(), args.output_dir / "fidelity-browser", resize=False)
    hybrid = extract(hybrid_copy, args.output_dir / "hybrid-browser-policy-1280x720", resize=True)
    write_fresh(
        args.output_dir / "hybrid-browser-normalization-receipt.json",
        {
            "schemaVersion": "authored-hybrid-browser-source-normalization/v1",
            "authorityDomain": "actual_product",
            "fidelityExportAttemptId": args.fidelity_export_attempt_id,
            "hybridExportAttemptId": args.hybrid_export_attempt_id,
            "fidelityPptxSha256": digest(args.fidelity_pptx.resolve()),
            "hybridBrowserSourcePptxSha256": digest(hybrid_copy),
            "operation": "rgba-lanczos-2560x1440-to-1280x720",
            "fidelitySlides": fidelity,
            "hybridSlides": hybrid,
        },
    )


if __name__ == "__main__":
    main()
