"""Canonical JSON and SHA-256 helpers."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any
import unicodedata

from pydantic import BaseModel

from models.authored_vnext.master.contract import DeckMasterContract, FontManifest


def canonical_json(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json", by_alias=True)
    else:
        payload = value
    return json.dumps(
        _canonical_value(payload),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_value(value: Any) -> Any:
    """NFC-normalize strings and make integral JSON numbers cross-runtime stable."""

    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON does not permit non-finite numbers")
        if value.is_integer():
            return int(value)
        return float(format(value, ".15g"))
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {
            unicodedata.normalize("NFC", str(key)): _canonical_value(item)
            for key, item in value.items()
        }
    return value


def canonical_digest(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    encoded = canonical_json(value).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def seal_font_manifest(manifest: FontManifest) -> FontManifest:
    payload = manifest.model_dump(mode="json", by_alias=True)
    payload.pop("digest", None)
    return manifest.model_copy(update={"digest": canonical_digest(payload)})


def seal_master_contract(contract: DeckMasterContract) -> DeckMasterContract:
    font_manifest = seal_font_manifest(contract.font_manifest)
    candidate = contract.model_copy(
        update={"font_manifest": font_manifest, "digest": None}
    )
    payload = candidate.model_dump(mode="json", by_alias=True)
    payload.pop("digest", None)
    return candidate.model_copy(update={"digest": canonical_digest(payload)})
