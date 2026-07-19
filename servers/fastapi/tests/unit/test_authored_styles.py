from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.ppt.endpoints.authored import AUTHORED_ROUTER
from utils.authored_styles import load_authored_styles, resolve_authored_style


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_load_authored_styles_returns_stable_id_order_and_full_briefs(tmp_path):
    _write(
        tmp_path / "zeta.yaml",
        "id: zeta\nname: Zeta\ndescription: Z\npreview:\n  bg: '#000'\n  accent: '#fff'\nbrief: zeta brief\n",
    )
    _write(
        tmp_path / "alpha.yaml",
        "id: alpha\nname: Alpha\ndescription: A\npreview:\n  bg: '#fff'\n  accent: '#000'\nbrief: alpha brief\n",
    )

    styles = load_authored_styles(tmp_path)

    assert [style.id for style in styles] == ["alpha", "zeta"]
    assert styles[0].brief == "alpha brief"


def test_load_authored_styles_warns_and_skips_broken_or_duplicate_files(tmp_path, caplog):
    _write(
        tmp_path / "default.yaml",
        "id: default\nname: Default\ndescription: D\npreview:\n  bg: '#fff'\n  accent: '#000'\nbrief: default brief\n",
    )
    _write(tmp_path / "broken.yaml", "id: [not valid")
    _write(
        tmp_path / "duplicate.yaml",
        "id: default\nname: Duplicate\ndescription: D\npreview:\n  bg: '#fff'\n  accent: '#000'\nbrief: duplicate brief\n",
    )
    _write(tmp_path / "missing-brief.yaml", "id: incomplete\nname: X\ndescription: X\npreview: {}\n")

    styles = load_authored_styles(tmp_path)

    assert [style.id for style in styles] == ["default"]
    assert caplog.text.count("Skipping invalid authored style") == 3


def test_resolve_authored_style_falls_back_to_default_for_unknown_id(tmp_path):
    _write(
        tmp_path / "default.yaml",
        "id: default\nname: Default\ndescription: D\npreview:\n  bg: '#fff'\n  accent: '#000'\nbrief: default brief\n",
    )

    assert resolve_authored_style("does-not-exist", tmp_path).id == "default"
    assert resolve_authored_style(None, tmp_path).id == "default"


def test_resolve_authored_style_uses_builtin_default_when_default_file_is_broken(tmp_path):
    _write(tmp_path / "default.yaml", "id: [not valid")

    style = resolve_authored_style("does-not-exist", tmp_path)

    assert style.id == "default"
    assert style.preview_bg == "#F8FAFC"


def test_authored_styles_api_hides_briefs_and_returns_catalogue():
    app = FastAPI()
    app.include_router(AUTHORED_ROUTER, prefix="/api/v1/ppt")
    client = TestClient(app)

    response = client.get("/api/v1/ppt/authored/styles")

    assert response.status_code == 200
    body = response.json()
    assert [style["id"] for style in body] == sorted(style["id"] for style in body)
    assert {style["id"] for style in body} == {
        "default",
        "exec-report",
        "strategic-navy",
        "editorial-tritone",
        "neo-grid-bold",
        "minimal-vellum",
    }
    assert all(set(style) == {"id", "name", "description", "preview"} for style in body)
    assert all(set(style["preview"]) == {"bg", "accent"} for style in body)
