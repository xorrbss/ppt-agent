"""One byte-preserving page identity for fresh SVG authoring and recovery."""
from __future__ import annotations

from utils.hashing import canonical_json_sha256


def svg_page_input_payload(*, content, style, index, count, instructions,
                           language, preset, writing_policy=None, header_page=None):
    value = {"content": content, "style": style, "index": index, "count": count,
             "instructions": instructions, "language": language, "preset": preset}
    if writing_policy is not None:
        value["writing_policy"] = writing_policy
    contract = (style or {}).get("common_header")
    if contract is not None or header_page is not None:
        from services.svg_common_header import (
            common_header_input_digest, validate_common_header_binding,
        )
        validate_common_header_binding(contract, header_page, index=index,
                                       count=count, content=content)
        value["common_header"] = common_header_input_digest(contract, header_page)
    return value


def svg_page_input_digest(*, assets, **kwargs):
    page = canonical_json_sha256(svg_page_input_payload(**kwargs), allow_nan=True)
    return canonical_json_sha256({"page": page, "assets": assets}, allow_nan=True)
