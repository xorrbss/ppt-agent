from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from utils.authored_style_converter import ConversionError, convert_path
from utils.authored_styles import AUTHORED_STYLES_DIRECTORY, load_authored_styles


FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "authored_style_converter"
    / "academic_edge.yaml"
)
SCRIPT = Path(__file__).parents[4] / "scripts" / "convert-authored-style.py"
BRIEF_SECTIONS = (
    "MOOD",
    "PALETTE",
    "TYPOGRAPHY",
    "LAYOUT SYSTEM",
    "SIGNATURE ELEMENTS",
    "DATA VISUALIZATION",
    "IMAGE DIRECTION",
    "SLIDE ARCHETYPES",
    "AVOID",
)


def _copy_fixture(target: Path, *, style_id: str | None = None) -> Path:
    content = FIXTURE.read_text(encoding="utf-8")
    if style_id is not None:
        content = f"id: {style_id}\n{content}"
    target.write_text(content, encoding="utf-8")
    return target


def _digest_catalogue() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(AUTHORED_STYLES_DIRECTORY.glob("*.yaml"))
    }


def test_converts_notebooklm_fixture_and_round_trips_current_loader(tmp_path):
    output = tmp_path / "결과 폴더"

    results = convert_path(FIXTURE, output)

    assert [result.style_id for result in results] == ["academic-edge"]
    target = output / "academic-edge.yaml"
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert list(data) == [
        "id",
        "name",
        "description",
        "category",
        "tags",
        "use_cases",
        "preview",
        "brief",
    ]
    assert data["name"] == "Academic Edge 스타일"
    assert "프레젠테이션 스타일입니다" in data["description"]
    assert data["category"] == "research"
    assert data["preview"] == {
        "bg": "#FFFFFF",
        "accent": "#D00000",
        "palette": ["#FFFFFF", "#D00000", "#F8F9FA", "#1A1A1A", "#2563EB", "#0F172A"],
        "variant": "notebooklm-research",
    }
    assert (
        tuple(
            line[:-1]
            for line in data["brief"].splitlines()
            if line.endswith(":") and line[:-1] in BRIEF_SECTIONS
        )
        == BRIEF_SECTIONS
    )
    archetypes = (
        data["brief"].split("SLIDE ARCHETYPES:\n", 1)[1].split("\n\nAVOID:", 1)[0]
    )
    assert len([line for line in archetypes.splitlines() if line.startswith("- ")]) >= 5

    loaded = load_authored_styles(output)
    assert len(loaded) == 1
    assert loaded[0].id == "academic-edge"
    assert loaded[0].brief == data["brief"]


def test_output_is_byte_deterministic(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"

    convert_path(FIXTURE, first)
    convert_path(FIXTURE, second)

    assert (first / "academic-edge.yaml").read_bytes() == (
        second / "academic-edge.yaml"
    ).read_bytes()


def test_brief_length_cap_rejects_oversized_source(tmp_path):
    from utils.authored_style_converter import MAX_BRIEF_CHARS

    data = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    # The MOOD section copies the source `description` verbatim; a huge one must
    # fail clean (bounded prompt-injection surface) instead of emitting a giant brief.
    data["description"] = "가" * (MAX_BRIEF_CHARS + 100)
    source = tmp_path / "oversized.yaml"
    source.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ConversionError, match="char limit"):
        convert_path(source, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_batch_is_sorted_and_accepts_yaml_and_yml(tmp_path):
    source_dir = tmp_path / "inputs"
    source_dir.mkdir()
    _copy_fixture(source_dir / "zeta.yml")
    _copy_fixture(source_dir / "alpha.yaml")
    (source_dir / "ignored.txt").write_text("ignored", encoding="utf-8")

    results = convert_path(source_dir, tmp_path / "outputs")

    assert [result.style_id for result in results] == ["alpha", "zeta"]
    assert sorted(path.name for path in (tmp_path / "outputs").iterdir()) == [
        "alpha.yaml",
        "zeta.yaml",
    ]


def test_batch_collision_fails_before_any_write(tmp_path):
    source_dir = tmp_path / "inputs"
    source_dir.mkdir()
    _copy_fixture(source_dir / "same_name.yaml")
    _copy_fixture(source_dir / "same-name.yml")
    output = tmp_path / "outputs"

    with pytest.raises(ConversionError, match="style id collision"):
        convert_path(source_dir, output)

    assert not output.exists()


@pytest.mark.parametrize(
    "bad_id",
    ["../escape", r"folder\\escape", "C:escape", "CON", "..."],
)
def test_rejects_unsafe_explicit_ids(tmp_path, bad_id):
    source = _copy_fixture(tmp_path / "input.yaml", style_id=bad_id)

    with pytest.raises(ConversionError, match="style id|unsafe"):
        convert_path(source, tmp_path / "output")


def test_rejects_non_string_explicit_id(tmp_path):
    source = _copy_fixture(tmp_path / "input.yaml")
    source.write_text(
        "id: [not, a, string]\n" + source.read_text(encoding="utf-8"), encoding="utf-8"
    )

    with pytest.raises(ConversionError, match="'id' must be a non-empty string"):
        convert_path(source, tmp_path / "output")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("- not-a-mapping\n", "root must be a mapping"),
        ("design_system: [broken]\n", "design_system.*mapping"),
        ("value: &shared x\nother: *shared\n", "aliases are not supported"),
        ("id: one\nid: two\n", "duplicate YAML key"),
        ("---\na: b\n---\nc: d\n", "single document"),
    ],
)
def test_malformed_input_fails_with_source_name(tmp_path, content, message):
    source = tmp_path / "broken.yaml"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ConversionError, match=message) as error:
        convert_path(source, tmp_path / "output")

    assert str(source) in str(error.value)


def test_cli_rejects_deep_yaml_without_traceback(tmp_path):
    source = tmp_path / "deep.yaml"
    source.write_text("{a:" * 1500 + "value" + "}" * 1500, encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(source),
            "--output",
            str(tmp_path / "output"),
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "nesting exceeds" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_dry_run_validates_without_creating_output(tmp_path):
    output = tmp_path / "not-created"

    results = convert_path(FIXTURE, output, dry_run=True)

    assert results[0].target == output / "academic-edge.yaml"
    assert not output.exists()


def test_overwrite_is_explicit_and_preserves_unrelated_files(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    target = output / "academic-edge.yaml"
    target.write_text("sentinel", encoding="utf-8")
    unrelated = output / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")

    with pytest.raises(ConversionError, match="use --overwrite"):
        convert_path(FIXTURE, output)
    assert target.read_text(encoding="utf-8") == "sentinel"

    convert_path(FIXTURE, output, overwrite=True)
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["id"] == "academic-edge"
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_single_file_output_and_source_self_replace_protection(tmp_path):
    source = _copy_fixture(tmp_path / "source file.yaml")
    target = tmp_path / "custom result.yaml"

    convert_path(source, target)
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["id"] == "source-file"

    with pytest.raises(ConversionError, match="source file"):
        convert_path(source, source, overwrite=True)


def test_case_insensitive_existing_filename_collision_is_never_overwritten(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "Academic-Edge.yaml").write_text("sentinel", encoding="utf-8")

    with pytest.raises(ConversionError, match="case-insensitive"):
        convert_path(FIXTURE, output, overwrite=True)


def test_batch_rejects_non_file_target_before_overwriting_any_file(tmp_path):
    source_dir = tmp_path / "inputs"
    source_dir.mkdir()
    _copy_fixture(source_dir / "alpha.yaml")
    _copy_fixture(source_dir / "zeta.yaml")
    output = tmp_path / "output"
    output.mkdir()
    alpha = output / "alpha.yaml"
    alpha.write_text("sentinel", encoding="utf-8")
    (output / "zeta.yaml").mkdir()

    with pytest.raises(ConversionError, match="not a regular file"):
        convert_path(source_dir, output, overwrite=True)

    assert alpha.read_text(encoding="utf-8") == "sentinel"


@pytest.mark.parametrize("filename", ["host:stream.yaml", "CON.extra.yaml"])
def test_rejects_windows_unsafe_custom_output_filename(tmp_path, filename):
    source = _copy_fixture(tmp_path / "input.yaml")

    with pytest.raises(ConversionError, match="unsafe output filename"):
        convert_path(source, tmp_path / filename, dry_run=True)


def test_rejects_uppercase_custom_output_suffix(tmp_path):
    source = _copy_fixture(tmp_path / "input.yaml")

    with pytest.raises(ConversionError, match="output file must use .yaml"):
        convert_path(source, tmp_path / "Custom.YAML", dry_run=True)


def test_default_conversion_never_changes_official_catalogue(tmp_path):
    before = _digest_catalogue()
    source = _copy_fixture(tmp_path / "default.yaml")

    with pytest.raises(ConversionError, match="use --overwrite"):
        convert_path(source, AUTHORED_STYLES_DIRECTORY)

    assert _digest_catalogue() == before
    assert len(load_authored_styles()) == 34


def test_cli_works_outside_repository_and_reports_errors_without_traceback(tmp_path):
    output = tmp_path / "cli output"
    command = [
        sys.executable,
        str(SCRIPT),
        str(FIXTURE),
        "--output",
        str(output),
        "--dry-run",
    ]

    completed = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True)

    assert completed.returncode == 0
    assert "총 1개 스타일 검증 완료" in completed.stdout
    assert not output.exists()


def test_cli_failure_has_nonzero_exit_and_no_traceback(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(tmp_path / "missing.yaml"),
            "-o",
            str(tmp_path / "out"),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 2
    assert "오류:" in completed.stderr
    assert "Traceback" not in completed.stderr
