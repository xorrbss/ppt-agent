"""Unit tests for the authored style sample generator (scripts/generate_authored_style_samples.py).

No network calls and no real generation: subprocess/git calls are monkeypatched
or never reached (dry-run), and PDF acceptance is exercised with injected page
data rather than a real rendered PDF (pypdfium2 text extraction from a
Pillow-built PDF is not reliable enough to assert on).
"""
from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image

from scripts.authored_style_samples_contact_sheet import ContactSheetRow, render_contact_sheet
from scripts.authored_style_samples_pdf import (
    PdfPageInfo,
    check_deck_acceptance,
    encode_webp_within_cap,
    thumb_dimensions,
)
from scripts.authored_style_samples_runner import build_ppt_agent_command, parse_ppt_agent_output
from scripts.authored_style_samples_topic_parser import extract_prompt, load_topic, parse_fixed_params
import scripts.generate_authored_style_samples as gen
from utils.authored_style_samples import load_style_samples, topic_prompt_digest

EXPECTED_TITLES = (
    "표지", "목차", "시장", "문제 정의", "제품", "작동 방식",
    "비즈니스 모델", "경쟁 비교", "고객 사례", "로드맵", "재무", "마무리",
)
EXPECTED_GUARDS = {1: ("루미나",), 2: ("목차",), 8: ("수작업",)}
_TITLES_BULLET = f"- Manifest slide titles, in order: {', '.join(EXPECTED_TITLES)}"
_GUARD_BULLET = "- Order guard (PDF text must contain): slide 1 `루미나`, slide 2 `목차`, slide 8 `수작업`"

TOPIC_TEXT = f"""# Demo topic

Some notes before the prompt.

## Prompt (Korean, 12 slides)

Fictitious company business plan. 루미나 로지스틱스 사업계획서.

## Fixed request parameters

- strategy: svg
{_TITLES_BULLET}
{_GUARD_BULLET}
"""


def _write_topic(tmp_path: Path, text: str = TOPIC_TEXT) -> Path:
    path = tmp_path / "topic.md"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Topic parsing
# ---------------------------------------------------------------------------


def test_extract_prompt_slice():
    prompt = extract_prompt(TOPIC_TEXT)
    assert prompt == "Fictitious company business plan. 루미나 로지스틱스 사업계획서."


def test_extract_prompt_matches_topic_prompt_digest(tmp_path: Path):
    topic_file = _write_topic(tmp_path)
    prompt = extract_prompt(topic_file.read_text(encoding="utf-8"))
    expected = f"sha256:{sha256(prompt.encode('utf-8')).hexdigest()}"
    assert topic_prompt_digest(topic_file) == expected


def test_parse_fixed_params_titles_and_guards():
    fixed = parse_fixed_params(TOPIC_TEXT)
    assert fixed.titles == EXPECTED_TITLES
    assert fixed.guard_keywords == EXPECTED_GUARDS


def test_load_topic_returns_prompt_and_fixed_params(tmp_path: Path):
    topic_file = _write_topic(tmp_path)
    prompt, fixed = load_topic(topic_file)
    assert prompt.startswith("Fictitious company business plan")
    assert len(fixed.titles) == 12


def test_parse_fixed_params_missing_bullet_raises():
    text = TOPIC_TEXT.replace(f"{_TITLES_BULLET}\n", "")
    with pytest.raises(ValueError, match="Manifest slide titles"):
        parse_fixed_params(text)


def test_extract_prompt_missing_markers_raises():
    with pytest.raises(ValueError, match="section markers"):
        extract_prompt("no headings here")


# ---------------------------------------------------------------------------
# Deck acceptance
# ---------------------------------------------------------------------------


def _page(index: int, width: float = 1280.0, height: float = 720.0, text: str = "") -> PdfPageInfo:
    return PdfPageInfo(index=index, width=width, height=height, text=text)


def test_check_deck_acceptance_passes_clean_deck():
    pages = [_page(i, text=f"slide {i} 루미나 목차 수작업") for i in range(1, 13)]
    problems = check_deck_acceptance(pages, 12, {1: ("루미나",), 2: ("목차",), 8: ("수작업",)})
    assert problems == []


def test_check_deck_acceptance_rejects_wrong_page_count():
    pages = [_page(i) for i in range(1, 11)]
    problems = check_deck_acceptance(pages, 12, {})
    assert any("12 pages" in p for p in problems)


def test_check_deck_acceptance_rejects_bad_aspect_ratio():
    pages = [_page(1, width=905, height=1280)]  # A4 portrait, not 16:9
    problems = check_deck_acceptance(pages, 1, {})
    assert any("aspect ratio" in p for p in problems)


def test_check_deck_acceptance_rejects_missing_guard_keyword():
    pages = [_page(1, text="no relevant words here")]
    problems = check_deck_acceptance(pages, 1, {1: ("루미나",)})
    assert any("루미나" in p for p in problems)


def test_check_deck_acceptance_rejects_missing_guard_slide():
    pages = [_page(1)]
    problems = check_deck_acceptance(pages, 1, {5: ("keyword",)})
    assert any("missing from the deck" in p for p in problems)


def test_check_deck_acceptance_tolerance_boundary():
    # 1280/725 ~= 1.7655, outside 1.778 +/- 0.01.
    pages = [_page(1, width=1280, height=725)]
    problems = check_deck_acceptance(pages, 1, {})
    assert problems


# ---------------------------------------------------------------------------
# Raster sizes / byte caps
# ---------------------------------------------------------------------------


def test_thumb_dimensions_landscape():
    assert thumb_dimensions(1280, 720) == (320, 180)


def test_thumb_dimensions_portrait():
    w, h = thumb_dimensions(905, 1280)
    assert h == 320
    assert w == round(905 * 320 / 1280)


def test_encode_webp_within_cap_fits_generously_capped_image():
    image = Image.new("RGB", (1280, 720), (30, 60, 90))
    payload, quality = encode_webp_within_cap(image, byte_cap=96 * 1024)
    assert payload is not None
    assert len(payload) <= 96 * 1024
    assert quality <= 88


def test_encode_webp_within_cap_fails_when_cap_too_small():
    # A high-entropy image compresses poorly at any WebP quality.
    import os

    pixels = os.urandom(320 * 180 * 3)
    image = Image.frombytes("RGB", (320, 180), pixels)
    payload, quality = encode_webp_within_cap(image, byte_cap=16)
    assert payload is None
    assert quality == 60


# ---------------------------------------------------------------------------
# Manifest shape (validated via the runtime loader)
# ---------------------------------------------------------------------------


def test_build_manifest_round_trips_through_load_style_samples(tmp_path: Path):
    style_id = "pilot-style"
    titles = [f"Slide {i}" for i in range(1, 13)]
    manifest = gen.build_manifest(
        style_id,
        titles,
        gen.CANVAS,
        generator_commit="a" * 40,
        style_digest="sha256:" + "b" * 64,
        topic_digest="sha256:" + "c" * 64,
    )
    directory = tmp_path / style_id
    directory.mkdir(parents=True)
    gen.write_json_lf(directory / "manifest.json", manifest)
    for index in range(1, 13):
        Image.new("RGB", (1280, 720)).save(directory / f"{index:02d}.webp", format="WEBP", quality=80)
        Image.new("RGB", (320, 180)).save(directory / f"{index:02d}.thumb.webp", format="WEBP", quality=80)

    samples = load_style_samples(style_id, tmp_path)
    assert samples is not None
    assert samples.style_id == style_id
    assert samples.canvas.width == 1280 and samples.canvas.height == 720
    assert [slide.title for slide in samples.slides] == titles
    assert samples.demo_topic_id == gen.DEMO_TOPIC_ID


def test_write_json_lf_uses_lf_line_endings(tmp_path: Path):
    path = tmp_path / "manifest.json"
    gen.write_json_lf(path, {"a": 1, "b": 2})
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert json.loads(raw.decode("utf-8")) == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# Clean-tree refusal
# ---------------------------------------------------------------------------


def test_ensure_clean_code_tree_raises_on_dirty_output(tmp_path: Path, monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd[:3] == ["git", "-C", str(tmp_path)]
        return subprocess.CompletedProcess(cmd, 0, stdout=" M servers/fastapi/foo.py\n", stderr="")

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    with pytest.raises(gen.GeneratorRefused, match="uncommitted changes"):
        gen.ensure_clean_code_tree(tmp_path)


def test_ensure_clean_code_tree_passes_on_clean_output(tmp_path: Path, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    gen.ensure_clean_code_tree(tmp_path)  # does not raise


def test_ensure_clean_code_tree_raises_on_git_failure(tmp_path: Path, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="not a git repository")

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    with pytest.raises(gen.GeneratorRefused, match="git status failed"):
        gen.ensure_clean_code_tree(tmp_path)


def test_get_generator_commit_returns_hex_sha(tmp_path: Path, monkeypatch):
    commit = "d" * 40

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{commit}\n", stderr="")

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    assert gen.get_generator_commit(tmp_path) == commit


def test_get_generator_commit_raises_on_unexpected_output(tmp_path: Path, monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="not-a-sha\n", stderr="")

    monkeypatch.setattr(gen.subprocess, "run", fake_run)
    with pytest.raises(gen.GeneratorRefused, match="unexpected commit hash"):
        gen.get_generator_commit(tmp_path)


# ---------------------------------------------------------------------------
# ppt-agent.mjs command / output parsing
# ---------------------------------------------------------------------------


def test_build_ppt_agent_command_shape(tmp_path: Path):
    out_dir = tmp_path / "out"
    command = build_ppt_agent_command(
        tmp_path, "프롬프트 내용", "kbs-company-proposal", out_dir, "http://127.0.0.1:8000", 1800
    )
    assert command[0] == "node"
    assert command[1] == str(tmp_path / "scripts" / "ppt-agent.mjs")
    assert "--async" in command
    assert command[command.index("--content") + 1] == "프롬프트 내용"
    assert command[command.index("--slides") + 1] == "12"
    assert command[command.index("--strategy") + 1] == "svg"
    assert command[command.index("--style") + 1] == "kbs-company-proposal"
    assert command[command.index("--export") + 1] == "pdf"
    assert command[command.index("--out") + 1] == str(out_dir)
    assert command[command.index("--timeout") + 1] == "1800"


def test_parse_ppt_agent_output_extracts_id_and_path():
    stdout = (
        "  topic:           test\n"
        "  presentation_id: abc-123\n"
        "  edit URL:        http://x\n"
        "  deck artifact:   /foo\n"
        "  export op:       op-1\n"
        "  downloaded to:   C:\\scratch\\pilot\\attempt-1\\presentation-abc-123.pdf\n"
    )
    presentation_id, downloaded_path = parse_ppt_agent_output(stdout)
    assert presentation_id == "abc-123"
    assert str(downloaded_path) == "C:\\scratch\\pilot\\attempt-1\\presentation-abc-123.pdf"


def test_parse_ppt_agent_output_missing_lines_returns_none():
    presentation_id, downloaded_path = parse_ppt_agent_output("nothing useful here\n")
    assert presentation_id is None
    assert downloaded_path is None


# ---------------------------------------------------------------------------
# Contact sheet
# ---------------------------------------------------------------------------


def test_contact_sheet_lists_failed_styles_first():
    rows = [
        ContactSheetRow(style_id="ok-style", ok=True, presentation_id="p1", thumbnails=("ok-style/01.thumb.webp",)),
        ContactSheetRow(style_id="bad-style", ok=False, problems=("expected 12 pages, got 10",)),
    ]
    html_text = render_contact_sheet(rows)
    assert html_text.index("bad-style") < html_text.index("ok-style")
    assert "expected 12 pages, got 10" in html_text
    assert "ok-style/01.thumb.webp" in html_text


# ---------------------------------------------------------------------------
# Dry run (no generation, no clean-tree check reached)
# ---------------------------------------------------------------------------


def test_dry_run_prints_plan_without_generating(tmp_path: Path, capsys, monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("dry-run must not invoke git/subprocess")

    monkeypatch.setattr(gen.subprocess, "run", fail_if_called)

    exit_code = gen.main(
        [
            "--code-tree",
            str(tmp_path / "nonexistent-code-tree"),
            "--scratch",
            str(tmp_path / "scratch"),
            "--samples-root",
            str(tmp_path / "samples"),
            "--styles",
            "default",
            "--dry-run",
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "style: default" in captured.out
    assert "ppt-agent.mjs" in captured.out
    assert "--content" in captured.out
    assert str(tmp_path / "samples" / "default") in captured.out


def test_dry_run_rejects_empty_style_list(tmp_path: Path, capsys):
    exit_code = gen.main(
        [
            "--code-tree",
            str(tmp_path),
            "--scratch",
            str(tmp_path / "scratch"),
            "--styles",
            " , ,",
            "--dry-run",
        ]
    )
    assert exit_code == 1
