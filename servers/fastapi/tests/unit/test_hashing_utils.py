"""Byte-contract gate for ``utils.hashing``.

The golden digests below pin Contract A (see the module docstring).  They
must never change: every migrated producer persists these bytes, and the
reference implementation it was measured against is asserted equal here.
"""

from __future__ import annotations

import json
import math

import pytest

from utils.hashing import (
    SHA256_PREFIX,
    canonical_json_bytes,
    canonical_json_digest,
    canonical_json_sha256,
    is_sha256_hex,
    sha256_file,
    sha256_hex,
    sha256_prefixed,
)

_FIXTURE = {"b": [1, 2.5, "한글", None, True], "a": {"z": "é", "y": 1e16}, "c": ""}
_GOLDEN_BYTES = (
    b'{"a":{"y":1e+16,"z":"\xc3\xa9"},"b":[1,2.5,"\xed\x95\x9c\xea\xb8\x80",null,true],"c":""}'
)
_GOLDEN_SHA256 = "2299bc57470fb74ebb0db8b7813994fb14f88047969ad67b626bf84c8dedcc89"
_GOLDEN_ASCII_SHA256 = "383c355684b9840fc7b10b6d63c9e90827d0329a33b518028690502928f9a7e3"
_GOLDEN_NAN_SHA256 = "1bfc5909d0e960432d9e81a6bd47ce1f89b8081c6bf880b85df7399f8704bff6"


def test_contract_a_golden_bytes_and_digest() -> None:
    assert canonical_json_bytes(_FIXTURE) == _GOLDEN_BYTES
    assert canonical_json_sha256(_FIXTURE) == _GOLDEN_SHA256
    assert canonical_json_digest(_FIXTURE) == SHA256_PREFIX + _GOLDEN_SHA256


def test_contract_a_matches_reference_implementation() -> None:
    from services.presentation_export_source_hashes import sha256_canonical_json

    assert sha256_canonical_json(_FIXTURE) == _GOLDEN_SHA256
    # Spelling the reference inline so the test does not depend on a later
    # migration of that module.
    reference = json.dumps(
        _FIXTURE, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    assert reference == _GOLDEN_BYTES


def test_ensure_ascii_fork_is_explicit_and_byte_identical() -> None:
    assert canonical_json_sha256(_FIXTURE, ensure_ascii=True) == _GOLDEN_ASCII_SHA256
    assert canonical_json_bytes(_FIXTURE, ensure_ascii=True) == json.dumps(
        _FIXTURE, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_allow_nan_fork_is_explicit() -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"k": math.nan})
    assert canonical_json_sha256({"k": math.nan}, allow_nan=True) == _GOLDEN_NAN_SHA256


def test_non_json_type_raises_type_error() -> None:
    with pytest.raises(TypeError):
        canonical_json_bytes({"k": object()})


def test_nfc_normalises_strings_and_keys_only_when_asked() -> None:
    decomposed = "é"
    assert canonical_json_bytes({"s": decomposed}) == b'{"s":"e\xcc\x81"}'
    assert canonical_json_bytes({"s": decomposed}, nfc=True) == b'{"s":"\xc3\xa9"}'
    assert canonical_json_bytes({decomposed: 1}, nfc=True) == b'{"\xc3\xa9":1}'


def test_bytes_digests_and_prefix() -> None:
    abc = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_hex(b"abc") == abc
    assert sha256_prefixed(b"abc") == "sha256:" + abc
    assert SHA256_PREFIX == "sha256:"


def test_is_sha256_hex() -> None:
    assert is_sha256_hex("0" * 64)
    assert not is_sha256_hex("0" * 63)
    assert not is_sha256_hex("A" * 64)
    assert not is_sha256_hex("sha256:" + "0" * 64)
    assert not is_sha256_hex(None)


def test_sha256_file_streams_whole_content(tmp_path) -> None:
    path = tmp_path / "blob.bin"
    payload = bytes(range(256)) * 5000
    path.write_bytes(payload)
    assert sha256_file(path) == sha256_hex(payload)
    assert sha256_file(str(path), chunk_size=7) == sha256_hex(payload)
