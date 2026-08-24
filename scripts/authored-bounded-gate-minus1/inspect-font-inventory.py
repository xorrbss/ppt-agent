import argparse
import hashlib
import io
import json
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont


DEFINITIONS = (
    ("Malgun Gothic", "Regular", "malgun.ttf"),
    ("Malgun Gothic", "Bold", "malgunbd.ttf"),
    ("Batang", "Regular", "batang.ttc"),
    ("BatangChe", "Regular", "batang.ttc"),
    ("Consolas", "Regular", "consola.ttf"),
    ("Consolas", "Bold", "consolab.ttf"),
)


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def names(font: TTFont) -> tuple[str, str]:
    family = font["name"].getDebugName(1) or font["name"].getDebugName(16) or ""
    subfamily = font["name"].getDebugName(2) or font["name"].getDebugName(17) or ""
    return family, subfamily


def inspect_face(font: TTFont, source: Path, index: int) -> dict:
    family, subfamily = names(font)
    stream = io.BytesIO()
    font.save(stream, reorderTables=False)
    codepoints = sorted({int(cp) for table in font["cmap"].tables for cp in table.cmap})
    cmap_bytes = b"".join(cp.to_bytes(4, "big") for cp in codepoints)
    fs_type = int(font["OS/2"].fsType)
    return {
        "family": family,
        "subfamily": subfamily,
        "sourceFile": source.name,
        "sourcePath": str(source),
        "sourceContainerSha256": sha256(source.read_bytes()),
        "sourceByteLength": source.stat().st_size,
        "ttcIndex": index,
        "extractedSfntSha256": sha256(stream.getvalue()),
        "extractedSfntByteLength": len(stream.getvalue()),
        "cmapCodepointSha256": sha256(cmap_bytes),
        "cmapCodepointCount": len(codepoints),
        "fsType": fs_type,
        "editableEmbeddingEligible": (fs_type & 0x000F) in (0, 8),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--font-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.font_root).resolve(strict=True)
    observed = []
    for wanted_family, wanted_subfamily, filename in DEFINITIONS:
        source = (root / filename).resolve(strict=True)
        if root not in source.parents:
            raise RuntimeError(f"font path escaped system root: {source}")
        if source.suffix.lower() == ".ttc":
            collection = TTCollection(str(source), lazy=False)
            candidates = [(index, font) for index, font in enumerate(collection.fonts)]
        else:
            candidates = [(0, TTFont(str(source), lazy=False))]
        matches = []
        for index, font in candidates:
            family, subfamily = names(font)
            if family.casefold() == wanted_family.casefold() and subfamily.casefold() == wanted_subfamily.casefold():
                matches.append((index, font))
        if len(matches) != 1:
            available = [(*names(font), index) for index, font in candidates]
            raise RuntimeError(f"expected one face {wanted_family}/{wanted_subfamily} in {source}, got {matches}; available={available}")
        index, font = matches[0]
        face = inspect_face(font, source, index)
        face["requiredFamily"] = wanted_family
        face["requiredSubfamily"] = wanted_subfamily
        observed.append(face)
    core = {"schemaVersion": "observed-font-inventory-receipt/v2", "systemFontRoot": str(root), "faces": observed}
    canonical = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    core["fontInventoryDigest"] = sha256(canonical)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f"refusing to overwrite {output}")
    output.write_text(json.dumps(core, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
