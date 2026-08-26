from __future__ import annotations

import hashlib
import json
from pathlib import PurePath
from typing import Any

from pydantic import BaseModel

from services.source_locked.contracts import AdmissionContractV1, IdempotencyPayloadV1, SourceMediaType


def sha256_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def canonical_json_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def basename(filename: str) -> str:
    # Upload names may contain either POSIX or Windows separators.
    return PurePath(filename.replace("\\", "/")).name


def media_type_from_filename(filename: str) -> SourceMediaType:
    suffix = PurePath(basename(filename)).suffix.lower().removeprefix(".")
    try:
        return SourceMediaType(suffix)
    except ValueError as exc:
        raise ValueError("source_locked v1 accepts exactly one .ppt, .pptx, or .pdf file") from exc


def build_idempotency_payload(
    admission_contract: AdmissionContractV1,
    source_snapshot_sha256: str,
) -> IdempotencyPayloadV1:
    return IdempotencyPayloadV1(
        admission_contract=admission_contract,
        source_snapshot_sha256=source_snapshot_sha256,
    )


def idempotency_payload_digest(
    admission_contract: AdmissionContractV1,
    source_snapshot_sha256: str,
) -> str:
    payload = build_idempotency_payload(admission_contract, source_snapshot_sha256)
    return sha256_digest(canonical_json_bytes(payload))
