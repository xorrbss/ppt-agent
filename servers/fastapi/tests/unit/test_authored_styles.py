from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.ppt.endpoints.authored import AUTHORED_ROUTER
from utils.authored_styles import load_authored_styles, resolve_authored_style


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


def test_authored_styles_api_hides_briefs_and_returns_catalogue():
    app = FastAPI()
    app.include_router(AUTHORED_ROUTER, prefix="/api/v1/ppt")
    client = TestClient(app)

    response = client.get("/api/v1/ppt/authored/styles")

    assert response.status_code == 200
    body = response.json()
    assert [style["id"] for style in body] == sorted(style["id"] for style in body)
    assert {
        "default",
        "exec-report",
        "strategic-navy",
        "editorial-tritone",
        "neo-grid-bold",
        "minimal-vellum",
    } <= {style["id"] for style in body}
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

    legacy_style = by_id["exec-report"]
    assert legacy_style["category"] == "general"
    assert legacy_style["tags"] == []
    assert legacy_style["use_cases"] == []
    assert set(legacy_style["preview"]) == {"bg", "accent"}
