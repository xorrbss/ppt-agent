"""Lossless font references for aggregate template artifacts.

Frames remain independently digestible after hydration. Only aggregate wire
representations replace repeated font assets with references to their font table.
"""
from __future__ import annotations


def _font_table(manifest: dict) -> dict[str, dict]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("assets"), (list, tuple)):
        raise ValueError("svg_template_font_reference_table_invalid")
    table = {}
    for asset in manifest.get("assets", ()):
        if not isinstance(asset, dict) or not isinstance(asset.get("asset_id"), str):
            raise ValueError("svg_template_font_reference_table_invalid")
        if not isinstance(asset.get("digest"), str) or not isinstance(asset.get("data_base64"), str):
            raise ValueError("svg_template_font_reference_table_invalid")
        if asset["asset_id"] in table:
            raise ValueError("svg_template_font_reference_duplicate")
        table[asset["asset_id"]] = asset
    return table


def _frame_fonts(frame: dict, table: dict[str, dict], *, compact: bool) -> dict:
    if not isinstance(frame, dict) or not isinstance(frame.get("font_manifest"), dict):
        raise ValueError("svg_template_font_reference_invalid")
    manifest = frame["font_manifest"]
    if not isinstance(manifest.get("assets"), (list, tuple)):
        raise ValueError("svg_template_font_reference_invalid")
    assets = []
    seen = set()
    for asset in manifest["assets"]:
        if not isinstance(asset, dict):
            raise ValueError("svg_template_font_reference_invalid")
        asset_id = asset.get("asset_id")
        if not isinstance(asset_id, str) or asset_id in seen:
            raise ValueError("svg_template_font_reference_duplicate")
        seen.add(asset_id)
        target = table.get(asset_id)
        if target is None or target["digest"] != asset.get("digest"):
            raise ValueError("svg_template_font_reference_missing_or_mismatched")
        # Accept only a bare reference or the exact full table entry. Metadata
        # cannot be overridden while borrowing the table's bytes or identity.
        if set(asset) != {"asset_id", "digest"} and asset != target:
            raise ValueError("svg_template_font_reference_metadata_mismatch")
        assets.append({"asset_id": asset_id, "digest": target["digest"]} if compact else dict(target))
    return {**frame, "font_manifest": {**manifest, "assets": assets}}


def pack_package_fonts(package: dict) -> dict:
    table = _font_table(package["font_manifest"])
    return {**package, "frames": [_frame_fonts(frame, table, compact=True) for frame in package["frames"]]}


def unpack_package_fonts(package: dict) -> dict:
    table = _font_table(package["font_manifest"])
    # Constructors may already contain validated Frame instances.
    return {**package, "frames": [
        _frame_fonts(frame, table, compact=False) if isinstance(frame, dict) else frame
        for frame in package["frames"]
    ]}


def pack_adaptation_fonts(adaptation: dict, package: dict) -> dict:
    return {**adaptation, "frame": _frame_fonts(adaptation["frame"], _font_table(package["font_manifest"]), compact=True)}


def unpack_adaptation_fonts(adaptation: dict, package: dict) -> dict:
    return {**adaptation, "frame": _frame_fonts(adaptation["frame"], _font_table(package["font_manifest"]), compact=False)}
