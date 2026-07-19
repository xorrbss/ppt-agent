import logging
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from api.v1.ppt.endpoints.authored import AUTHORED_ROUTER
from utils.authored_styles import (
    AUTHORED_STYLE_CATEGORIES,
    AUTHORED_STYLES_DIRECTORY,
    load_authored_styles,
    resolve_authored_style,
)


EXPECTED_AUTHORED_STYLE_IDS = {
    "academic-edge",
    "architectural-portfolio",
    "botanical-journal",
    "broadside",
    "clinical-precision",
    "cobalt-editorial",
    "cyber-ai",
    "default",
    "editorial-tritone",
    "exec-report",
    "geometric-mono",
    "groovy-70s",
    "liquid-executive",
    "luxury-editorial",
    "minimal-vellum",
    "neo-grid-bold",
    "neon-venture",
    "prestige-gold",
    "prime-noir",
    "prismatic-tech",
    "project-launch",
    "scholars-journal",
    "science-sketch",
    "silicon-refined",
    "soft-editorial",
    "startup-aura",
    "strategic-insight",
    "strategic-navy",
    "structured-mint",
    "visual-discovery",
}
TECHNOLOGY_STYLE_IDS = {
    "cyber-ai",
    "neon-venture",
    "prismatic-tech",
    "project-launch",
    "startup-aura",
    "visual-discovery",
}
REQUIRED_BRIEF_SECTIONS = (
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
PLACEHOLDER_PATTERN = re.compile(
    r"\b(?:TODO|TBD|FIXME|placeholder)\b|lorem ipsum|"
    r"\{\{.*?\}\}|\$\{[^}]+\}|"
    r"<(?:placeholder|replace)[^>]*>|\[(?:placeholder|replace)[^]]*\]",
    re.IGNORECASE | re.DOTALL,
)
BRIEF_SECTION_PATTERN = re.compile(
    r"(?m)^[ \t]*([A-Z][A-Z ]*?[A-Z])[ \t]*(?::(?:[ \t]|$)|$)"
)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_authored_styles_keeps_legacy_yaml_compatible(tmp_path):
    _write(
        tmp_path / "zeta.yaml",
        "id: zeta\nname: Zeta\ndescription: Z\npreview:\n  bg: '#000000'\n  accent: '#FFFFFF'\nbrief: zeta brief\n",
    )
    _write(
        tmp_path / "alpha.yaml",
        "id: alpha\nname: Alpha\ndescription: A\npreview:\n  bg: '#FFFFFF'\n  accent: '#000000'\nbrief: alpha brief\n",
    )

    styles = load_authored_styles(tmp_path)

    assert [style.id for style in styles] == ["alpha", "zeta"]
    assert styles[0].brief == "alpha brief"
    assert styles[0].category == "general"
    assert styles[0].tags == []
    assert styles[0].use_cases == []
    assert styles[0].preview_palette is None
    assert styles[0].preview_variant is None
    assert styles[0].public_dict() == {
        "id": "alpha",
        "name": "Alpha",
        "description": "A",
        "category": "general",
        "tags": [],
        "use_cases": [],
        "preview": {"bg": "#FFFFFF", "accent": "#000000"},
    }


def test_load_authored_styles_parses_rich_public_metadata(tmp_path):
    _write(
        tmp_path / "research.yaml",
        """id: research
name: Research
description: Evidence-led
category: research
tags:
  - Evidence
  - Academic
use_cases:
  - Literature review
preview:
  bg: '#F8FAFC'
  accent: '#2563EB'
  palette:
    - '#F8FAFC'
    - '#0F172A'
    - '#2563EB'
  variant: evidence-grid
brief: structured private brief
""",
    )

    style = load_authored_styles(tmp_path)[0]

    assert style.category == "research"
    assert style.tags == ["Evidence", "Academic"]
    assert style.use_cases == ["Literature review"]
    assert style.preview_palette == ["#F8FAFC", "#0F172A", "#2563EB"]
    assert style.preview_variant == "evidence-grid"
    assert style.public_dict() == {
        "id": "research",
        "name": "Research",
        "description": "Evidence-led",
        "category": "research",
        "tags": ["Evidence", "Academic"],
        "use_cases": ["Literature review"],
        "preview": {
            "bg": "#F8FAFC",
            "accent": "#2563EB",
            "palette": ["#F8FAFC", "#0F172A", "#2563EB"],
            "variant": "evidence-grid",
        },
    }


def test_load_authored_styles_warns_and_skips_broken_or_duplicate_files(tmp_path, caplog):
    _write(
        tmp_path / "default.yaml",
        "id: default\nname: Default\ndescription: D\npreview:\n  bg: '#FFFFFF'\n  accent: '#000000'\nbrief: default brief\n",
    )
    _write(tmp_path / "broken.yaml", "id: [not valid")
    _write(
        tmp_path / "duplicate.yaml",
        "id: default\nname: Duplicate\ndescription: D\npreview:\n  bg: '#FFFFFF'\n  accent: '#000000'\nbrief: duplicate brief\n",
    )
    _write(tmp_path / "missing-brief.yaml", "id: incomplete\nname: X\ndescription: X\npreview: {}\n")

    styles = load_authored_styles(tmp_path)

    assert [style.id for style in styles] == ["default"]
    assert caplog.text.count("Skipping invalid authored style") == 3


def test_load_authored_styles_validates_all_preview_colors(tmp_path, caplog):
    base = "id: {id}\nname: Invalid\ndescription: D\npreview:\n{preview}\nbrief: private\n"
    invalid_previews = {
        "short-bg": "  bg: '#FFF'\n  accent: '#000000'",
        "missing-hash": "  bg: 'FFFFFF'\n  accent: '#000000'",
        "bad-accent": "  bg: '#FFFFFF'\n  accent: '#12345G'",
        "bad-palette": (
            "  bg: '#FFFFFF'\n  accent: '#000000'\n"
            "  palette:\n    - '#2563EB'\n    - '#12345G'"
        ),
    }
    for style_id, preview in invalid_previews.items():
        _write(
            tmp_path / f"{style_id}.yaml",
            base.format(id=style_id, preview=preview),
        )

    assert load_authored_styles(tmp_path) == []
    assert caplog.text.count("Skipping invalid authored style") == len(
        invalid_previews
    )
    assert "must be a #RRGGBB color" in caplog.text


def test_load_authored_styles_rejects_invalid_metadata_shapes(tmp_path, caplog):
    base = (
        "id: {id}\nname: Invalid\ndescription: D\n{metadata}"
        "preview:\n  bg: '#FFFFFF'\n  accent: '#000000'\nbrief: private\n"
    )
    invalid_metadata = {
        "bad-category": "category: sales\n",
        "bad-tags": "tags: not-a-list\n",
        "bad-use-cases": "use_cases:\n  - valid\n  - 42\n",
        "bad-variant": "",
    }
    for style_id, metadata in invalid_metadata.items():
        if style_id == "bad-variant":
            content = (
                "id: bad-variant\nname: Invalid\ndescription: D\npreview:\n"
                "  bg: '#FFFFFF'\n  accent: '#000000'\n  variant: Not_English\n"
                "brief: private\n"
            )
        else:
            content = base.format(id=style_id, metadata=metadata)
        _write(tmp_path / f"{style_id}.yaml", content)

    assert load_authored_styles(tmp_path) == []
    assert caplog.text.count("Skipping invalid authored style") == len(
        invalid_metadata
    )


def test_resolve_authored_style_falls_back_to_default_for_empty_or_unknown_id(tmp_path):
    _write(
        tmp_path / "default.yaml",
        "id: default\nname: Default\ndescription: D\npreview:\n  bg: '#FFFFFF'\n  accent: '#000000'\nbrief: default brief\n",
    )

    for requested_id in (None, "", "   ", "does-not-exist"):
        style = resolve_authored_style(requested_id, tmp_path)
        assert style.id == "default"
        assert style.brief == "default brief"


def test_resolve_authored_style_uses_builtin_default_when_default_file_is_broken(tmp_path):
    _write(tmp_path / "default.yaml", "id: [not valid")

    style = resolve_authored_style("does-not-exist", tmp_path)

    assert style.id == "default"
    assert style.name == "기본 블루프린트"
    assert style.description == "깔끔한 흰 바탕과 브랜드 블루로 구성한 범용 프레젠테이션 스타일"
    assert style.preview_bg == "#F8FAFC"


def test_authored_catalogue_has_exactly_30_complete_contracts(caplog):
    sources = sorted(AUTHORED_STYLES_DIRECTORY.glob("*.yaml"))

    with caplog.at_level(logging.WARNING, logger="utils.authored_styles"):
        styles = load_authored_styles()

    assert len(sources) == len(styles) == len(EXPECTED_AUTHORED_STYLE_IDS) == 30
    assert {source.stem for source in sources} == EXPECTED_AUTHORED_STYLE_IDS
    assert {style.id for style in styles} == EXPECTED_AUTHORED_STYLE_IDS
    assert not [
        record
        for record in caplog.records
        if record.name == "utils.authored_styles"
        and record.levelno >= logging.WARNING
    ]

    for source in sources:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
        style_id = data["id"]

        assert source.stem == style_id
        assert data["category"] in AUTHORED_STYLE_CATEGORIES
        if style_id == "clinical-precision":
            assert data["category"] == "research"
        if style_id in TECHNOLOGY_STYLE_IDS:
            assert data["category"] == "technology"
        assert data["tags"] and all(
            isinstance(tag, str) and tag.strip() for tag in data["tags"]
        )
        assert data["use_cases"] and all(
            isinstance(use_case, str) and use_case.strip()
            for use_case in data["use_cases"]
        )
        assert re.search(r"[가-힣]", data["name"])
        assert re.search(r"[가-힣]", data["description"])

        preview = data["preview"]
        assert set(preview) == {"bg", "accent", "palette", "variant"}
        assert len(preview["palette"]) >= 3
        assert preview["variant"]

        brief = data["brief"]
        assert tuple(BRIEF_SECTION_PATTERN.findall(brief)) == REQUIRED_BRIEF_SECTIONS
        for section in REQUIRED_BRIEF_SECTIONS:
            headings = re.findall(
                rf"(?m)^[ \t]*{re.escape(section)}[ \t]*:?(?:[ \t]|$)",
                brief,
            )
            assert len(headings) == 1, f"{style_id}: {section} section"

        archetypes = re.search(
            r"(?ms)^[ \t]*SLIDE ARCHETYPES[ \t]*:?[ \t]*(.*?)"
            r"(?=^[ \t]*AVOID[ \t]*:?)",
            brief,
        )
        assert archetypes, f"{style_id}: SLIDE ARCHETYPES body"
        archetype_labels = re.findall(
            r"(?im)(?:^|[;\n])[ \t]*(?:-\s*)?"
            r"([a-z][a-z0-9 -]*?)[ \t]*:",
            archetypes.group(1),
        )
        assert len(set(archetype_labels)) >= 5, f"{style_id}: slide archetypes"
        assert not PLACEHOLDER_PATTERN.search(source.read_text(encoding="utf-8"))


def test_authored_styles_api_hides_briefs_and_returns_exact_catalogue():
    app = FastAPI()
    app.include_router(AUTHORED_ROUTER, prefix="/api/v1/ppt")
    client = TestClient(app)

    response = client.get("/api/v1/ppt/authored/styles")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 30
    assert [style["id"] for style in body] == sorted(style["id"] for style in body)
    assert {style["id"] for style in body} == EXPECTED_AUTHORED_STYLE_IDS
    assert all(
        set(style)
        == {
            "id",
            "name",
            "description",
            "category",
            "tags",
            "use_cases",
            "preview",
        }
        for style in body
    )
    assert all("brief" not in style for style in body)
    assert all({"bg", "accent"} <= set(style["preview"]) for style in body)

    by_id = {style["id"]: style for style in body}
    assert by_id["default"]["name"] == "기본 블루프린트"
    assert by_id["default"]["description"] == (
        "깔끔한 흰 바탕과 브랜드 블루로 구성한 범용 프레젠테이션 스타일"
    )
    expected_categories = {
        "default": "general",
        "strategic-navy": "business",
        "editorial-tritone": "editorial",
        "neo-grid-bold": "creative",
        "minimal-vellum": "editorial",
    }
    for style_id, category in expected_categories.items():
        style = by_id[style_id]
        assert style["category"] == category
        assert style["tags"]
        assert style["use_cases"]
        assert len(style["preview"]["palette"]) >= 3
        assert style["preview"]["variant"]

    exec_report = by_id["exec-report"]
    assert exec_report["category"] == "business"
    assert exec_report["tags"] == ["executive", "finance", "data"]
    assert exec_report["use_cases"] == ["경영 실적 보고", "이사회 보고", "투자 검토"]
    assert exec_report["preview"] == {
        "bg": "#051C2C",
        "accent": "#00A3E0",
        "palette": ["#051C2C", "#FFFFFF", "#2E3338", "#00A3E0", "#A7B0B7"],
        "variant": "executive-ledger",
    }
