import pytest

from utils.env_settings import tooling
from utils.env_settings.tooling import get_external_tool_paths, get_liteparse_settings


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in tooling.ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_liteparse_defaults():
    cfg = get_liteparse_settings(default_dpi=120, default_num_workers=3)
    assert cfg.node_binary == "node"
    assert cfg.runner_path is None
    assert cfg.runner_output is None
    assert cfg.ocr_server_url is None
    assert cfg.tessdata_path is None
    assert (cfg.dpi, cfg.num_workers) == (120, 3)


def test_liteparse_blank_node_binary_preserved(monkeypatch):
    monkeypatch.setenv("LITEPARSE_NODE_BINARY", "")
    assert get_liteparse_settings().node_binary == ""


def test_liteparse_numeric_clamp_and_invalid(monkeypatch):
    monkeypatch.setenv("LITEPARSE_DPI", "10")
    monkeypatch.setenv("LITEPARSE_NUM_WORKERS", "999")
    cfg = get_liteparse_settings(default_dpi=120, default_num_workers=3)
    assert (cfg.dpi, cfg.num_workers) == (72, 64)
    monkeypatch.setenv("LITEPARSE_DPI", "abc")
    monkeypatch.setenv("LITEPARSE_NUM_WORKERS", "  ")
    cfg = get_liteparse_settings(default_dpi=120, default_num_workers=3)
    assert (cfg.dpi, cfg.num_workers) == (120, 3)


def test_liteparse_raw_strings(monkeypatch):
    monkeypatch.setenv("LITEPARSE_RUNNER_PATH", "/r.mjs")
    monkeypatch.setenv("LITEPARSE_RUNNER_OUTPUT", " JSON ")
    monkeypatch.setenv("LITEPARSE_OCR_SERVER_URL", " http://x ")
    monkeypatch.setenv("LITEPARSE_TESSDATA_PATH", "/t")
    cfg = get_liteparse_settings()
    assert cfg.runner_path == "/r.mjs"
    assert cfg.runner_output == " JSON "
    assert cfg.ocr_server_url == " http://x "
    assert cfg.tessdata_path == "/t"


def test_external_tool_paths(monkeypatch):
    assert get_external_tool_paths() == tooling.ExternalToolPaths(*([None] * 8))
    monkeypatch.setenv("TESSERACT_CMD", "tess")
    monkeypatch.setenv("TESSDATA_PREFIX", "/p")
    monkeypatch.setenv("PRESENTON_APP_ROOT", "/app")
    monkeypatch.setenv("AUTHORED_OCR_TESSDATA_PATH", "/o")
    monkeypatch.setenv("SOFFICE_PATH", "so")
    monkeypatch.setenv("IMAGEMAGICK_BINARY", "im")
    monkeypatch.setenv("NODE_BINARY", "nd")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    paths = get_external_tool_paths()
    assert paths == tooling.ExternalToolPaths(
        "tess", "/p", "/app", "/o", "so", "im", "nd", "0"
    )


def test_tessdata_fallback_chain(monkeypatch, tmp_path):
    from services.liteparse_service import LiteParseService

    resolve = LiteParseService._resolve_tessdata_path
    monkeypatch.setenv("TESSDATA_PREFIX", " /prefix ")
    assert resolve(object.__new__(LiteParseService)) == "/prefix"
    monkeypatch.setenv("LITEPARSE_TESSDATA_PATH", "/lp")
    assert resolve(object.__new__(LiteParseService)) == "/lp"
