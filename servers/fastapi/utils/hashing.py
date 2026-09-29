"""SHA-256 digest primitives shared by every persisted-digest producer.

Digests are persisted (receipts, sidecars, artifact keys, design-pack pins),
so the byte contract of each producer is frozen.  This module owns the
building blocks and documents the two reference contracts that the
repository's copies were measured against.

Contract A: plain canonical JSON (``canonical_json_bytes`` defaults)
--------------------------------------------------------------------
Reference: ``services.presentation_export_source_hashes.sha256_canonical_json``.

``json.dumps(value, allow_nan=False, ensure_ascii=False,
separators=(",", ":"), sort_keys=True).encode("utf-8")``

* keys sorted by Python ``str`` ordering (code points), not UTF-16 units;
* no whitespace; non-ASCII characters emitted raw (UTF-8);
* floats use Python ``repr`` (``1.0`` stays ``1.0``, ``1e+16`` stays so);
* ``NaN``/``Infinity`` raise ``ValueError``;
* strings are *not* Unicode-normalised;
* non-JSON types raise ``TypeError`` (no ``default=``).

Contract B: ``canonical-json-unicode-nfc-f64-v1``
-------------------------------------------------
Reference: ``services.authored_vnext.semantic.canonical.canonical_json``.
This is a hand-rolled serializer, not ``json.dumps``: strings and keys are
NFC-normalised, keys sort by their UTF-16-BE encoding (JavaScript order),
integral floats collapse to integers and other floats print in a shortest
``coefficient e exponent`` form shared with the TypeScript runtime, and
unsupported types raise ``SemanticContractError``.  It is deliberately *not*
reimplemented here: its consumers (``deck_ir/canonical.py``,
``master/digests.py`` with its own ``.15g`` float rule, ``export_payload.py``)
stay on their versioned serializer.  ``nfc=True`` below only NFC-normalises
strings before Contract A and is *not* Contract B.

Both contracts feed ``sha256`` and are usually emitted with the
``"sha256:"`` prefix (``sha256_prefixed``); a few persisted columns store
the bare 64-hex form (``sha256_hex``).
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

SHA256_PREFIX = "sha256:"

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {_nfc(str(key)): _nfc(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_nfc(item) for item in value]
    return value


def canonical_json_bytes(
    value: Any,
    *,
    allow_nan: bool = False,
    ensure_ascii: bool = False,
    nfc: bool = False,
) -> bytes:
    """Serialize ``value`` under Contract A and return the UTF-8 bytes.

    ``allow_nan=True`` and ``ensure_ascii=True`` reproduce, byte for byte,
    the copies that omitted ``allow_nan`` or asked for ASCII output; callers
    pass them explicitly so the fork is visible at the call site.  ``nfc``
    NFC-normalises every string and key first (see the module docstring for
    why that is not Contract B).
    """

    if nfc:
        value = _nfc(value)
    return json.dumps(
        value,
        allow_nan=allow_nan,
        ensure_ascii=ensure_ascii,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Bare lowercase 64-hex SHA-256 of ``data``."""

    return sha256(data).hexdigest()


def sha256_prefixed(data: bytes) -> str:
    """``"sha256:<hex>"`` of ``data``."""

    return SHA256_PREFIX + sha256(data).hexdigest()


def canonical_json_sha256(value: Any, **kwargs: Any) -> str:
    """Bare hex SHA-256 of ``canonical_json_bytes(value, **kwargs)``."""

    return sha256_hex(canonical_json_bytes(value, **kwargs))


def canonical_json_digest(value: Any, **kwargs: Any) -> str:
    """Prefixed SHA-256 of ``canonical_json_bytes(value, **kwargs)``."""

    return sha256_prefixed(canonical_json_bytes(value, **kwargs))


def is_sha256_hex(text: object) -> bool:
    """Whether ``text`` is exactly 64 lowercase hex characters."""

    return isinstance(text, str) and _SHA256_HEX.fullmatch(text) is not None


def sha256_file(path: Path | str, chunk_size: int = 1024 * 1024) -> str:
    """Bare hex SHA-256 of a file, streamed in ``chunk_size`` blocks."""

    digest = sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "SHA256_PREFIX",
    "canonical_json_bytes",
    "canonical_json_digest",
    "canonical_json_sha256",
    "is_sha256_hex",
    "sha256_file",
    "sha256_hex",
    "sha256_prefixed",
]
