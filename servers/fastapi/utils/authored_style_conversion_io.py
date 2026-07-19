"""Filesystem preflight and writes for authored style conversion."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from utils.authored_style_converter import (
    ConversionError,
    YAML_SUFFIXES,
    _read_yaml,
    _serialize,
    convert_document,
)
from utils.authored_styles import load_authored_styles


_WINDOWS_RESERVED_NAMES = (
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_WINDOWS_FORBIDDEN_FILENAME_CHARACTERS = set('<>:"/\\|?*')


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    target: Path
    style_id: str


@dataclass(frozen=True)
class _PreparedStyle:
    source: Path
    target: Path
    data: dict[str, Any]
    content: bytes


def _round_trip(prepared: Sequence[_PreparedStyle]) -> None:
    with tempfile.TemporaryDirectory(prefix="authored-style-converter-") as directory:
        validation_dir = Path(directory)
        for item in prepared:
            (validation_dir / f"{item.data['id']}.yaml").write_bytes(item.content)
        loaded = load_authored_styles(validation_dir)
    expected_ids = [str(item.data["id"]) for item in prepared]
    if [style.id for style in loaded] != sorted(expected_ids):
        raise ConversionError("generated YAML failed authored style loader round-trip")
    expected_by_id = {str(item.data["id"]): item.data for item in prepared}
    for style in loaded:
        expected = expected_by_id[style.id]
        actual = style.public_dict()
        if (
            actual != {key: expected[key] for key in actual}
            or style.brief != expected["brief"]
        ):
            raise ConversionError(
                f"generated style '{style.id}' changed during loader round-trip"
            )


def _sources(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() not in YAML_SUFFIXES:
            raise ConversionError(f"{input_path}: input must use .yaml or .yml")
        return [input_path]
    if not input_path.is_dir():
        raise ConversionError(
            f"{input_path}: input does not exist or is not a file/directory"
        )
    sources = sorted(
        (
            path
            for path in input_path.iterdir()
            if path.is_file() and path.suffix.lower() in YAML_SUFFIXES
        ),
        key=lambda path: (path.name.casefold(), path.name),
    )
    if not sources:
        raise ConversionError(
            f"{input_path}: directory contains no .yaml or .yml files"
        )
    return sources


def _targets(
    sources: Sequence[Path],
    output_path: Path,
    data: Sequence[Mapping[str, Any]],
) -> list[Path]:
    if len(sources) == 1 and output_path.suffix.lower() in YAML_SUFFIXES:
        if output_path.suffix != ".yaml":
            raise ConversionError(f"{output_path}: output file must use .yaml")
        windows_base_name = output_path.name.split(".", 1)[0].casefold()
        if (
            windows_base_name in _WINDOWS_RESERVED_NAMES
            or output_path.name.rstrip(" .") != output_path.name
            or any(ord(character) < 32 for character in output_path.name)
            or any(
                character in _WINDOWS_FORBIDDEN_FILENAME_CHARACTERS
                for character in output_path.name
            )
        ):
            raise ConversionError(f"{output_path}: unsafe output filename")
        return [output_path]
    if len(sources) > 1 and output_path.suffix.lower() in YAML_SUFFIXES:
        raise ConversionError(
            f"{output_path}: batch conversion requires an output directory"
        )
    return [output_path / f"{item['id']}.yaml" for item in data]


def _preflight(prepared: Sequence[_PreparedStyle], overwrite: bool) -> None:
    ids: dict[str, Path] = {}
    targets: dict[str, Path] = {}
    for item in prepared:
        folded_id = str(item.data["id"]).casefold()
        if folded_id in ids:
            raise ConversionError(
                f"style id collision: {ids[folded_id]} and {item.source}"
            )
        ids[folded_id] = item.source
        folded_target = str(item.target.resolve(strict=False)).casefold()
        if folded_target in targets:
            raise ConversionError(
                f"output collision: {targets[folded_target]} and {item.target}"
            )
        targets[folded_target] = item.target
        if item.source.resolve() == item.target.resolve(strict=False):
            raise ConversionError(f"refusing to replace source file: {item.source}")

        parent = item.target.parent
        if parent.exists():
            if not parent.is_dir():
                raise ConversionError(f"{parent}: output parent is not a directory")
            matching = [
                entry
                for entry in parent.iterdir()
                if entry.name.casefold() == item.target.name.casefold()
            ]
            exact = [entry for entry in matching if entry.name == item.target.name]
            if len(matching) > 1 or (matching and not exact):
                raise ConversionError(
                    f"{item.target}: case-insensitive filename collision"
                )
            if exact:
                existing = exact[0]
                if not existing.is_file() or existing.is_symlink():
                    raise ConversionError(
                        f"{item.target}: output exists but is not a regular file"
                    )
                if not overwrite:
                    raise ConversionError(
                        f"{item.target}: output exists (use --overwrite)"
                    )


def convert_path(
    input_path: Path | str,
    output_path: Path | str,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> list[ConversionResult]:
    input_path = Path(input_path).expanduser().resolve()
    output_path = Path(output_path).expanduser().resolve(strict=False)
    sources = _sources(input_path)
    converted = [convert_document(_read_yaml(source), source) for source in sources]
    targets = _targets(sources, output_path, converted)
    prepared = [
        _PreparedStyle(source, target, data, _serialize(data))
        for source, target, data in zip(sources, targets, converted)
    ]
    _preflight(prepared, overwrite)
    _round_trip(prepared)

    if not dry_run:
        for item in prepared:
            item.target.parent.mkdir(parents=True, exist_ok=True)
        staged: list[tuple[Path, Path]] = []
        try:
            for item in prepared:
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{item.target.name}.",
                    suffix=".tmp",
                    dir=item.target.parent,
                )
                os.close(descriptor)
                temporary = Path(temporary_name)
                temporary.write_bytes(item.content)
                staged.append((temporary, item.target))
            for temporary, target in staged:
                temporary.replace(target)
        finally:
            for temporary, _ in staged:
                temporary.unlink(missing_ok=True)

    return [
        ConversionResult(item.source, item.target, str(item.data["id"]))
        for item in prepared
    ]
