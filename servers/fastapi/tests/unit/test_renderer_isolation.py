import asyncio
import base64
import io
import json
import pathlib
import socket
import sys

import pytest
from PIL import Image

from utils import (
    renderer_isolation,
    slide_capture,
    slide_capture_pipe,
    slide_capture_worker_client,
)


SAFE_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:1280px;height:720px;background:#fff}.card{position:absolute;left:20px;top:20px;width:240px;height:80px;background:#2563eb;color:white;padding:12px}
</style></head><body><main class="card"><h1>Safe authored</h1></main></body></html>"""


def _png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (1280, 720), "white").save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize(
    "hostile",
    [
        '<script>fetch("http://127.0.0.1:9")</script>',
        '<img src="file:///etc/passwd">',
        '<iframe src="http://localhost:9"></iframe>',
        '<div style="background:url(https://egress.invalid/x)">x</div>',
        '<style>@font-face{font-family:x;src:url(data:font/woff2;base64,QQ==)}</style>',
        r'<style>.x{background-image:u\72l(http://127.0.0.1:9/x)}</style>',
        '<svg><rect width="10" height="10" fill="url(http://127.0.0.1:9/x)"></rect></svg>',
        r'<svg><rect width="10" height="10" fill="u\72l(#local)"></rect></svg>',
        '<object data="../../secret"></object>',
        '<img width="999999" height="999999" src="data:image/png;base64,QQ==">',
    ],
)
def test_hostile_corpus_shapes_are_rejected_before_chrome(hostile: str) -> None:
    with pytest.raises(renderer_isolation.RendererPolicyError):
        renderer_isolation.admit_authored_html(hostile)


def test_safe_fixture_is_admitted_with_nonce_csp() -> None:
    admitted = renderer_isolation.admit_authored_html(SAFE_HTML)
    assert admitted.source_digest.startswith("sha256:")
    assert admitted.node_count > 0
    assert "Content-Security-Policy" in admitted.html
    assert f"script-src &#x27;nonce-{admitted.nonce}&#x27;" in admitted.html
    assert "connect-src &#x27;none&#x27;" in admitted.html
    assert "img-src data:" in admitted.html


def test_safe_inline_svg_namespace_opacity_and_presentation_attrs_are_admitted() -> None:
    source = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80"
    width="120" height="80" preserveAspectRatio="xMidYMid meet" focusable="false">
      <g transform="translate(4 6)" fill="none" stroke="#2563eb" stroke-width="2"
         stroke-opacity=".75" stroke-linecap="round" stroke-linejoin="round"
         stroke-dasharray="4 2">
        <path d="M0 20 L40 0 L80 20 Z" fill="#93c5fd" fill-opacity="0.25"
              fill-rule="evenodd" clip-rule="evenodd"
              vector-effect="non-scaling-stroke"></path>
      </g>
    </svg>"""

    admitted = renderer_isolation.admit_authored_html(source)

    assert admitted.node_count == 3
    assert 'xmlns="http://www.w3.org/2000/svg"' in admitted.html
    assert 'stroke-opacity=".75"' in admitted.html
    assert 'fill-opacity="0.25"' in admitted.html


@pytest.mark.parametrize(
    "hostile_svg",
    [
        '<svg xmlns="https://attacker.test/svg"><path d="M0 0"></path></svg>',
        '<svg xmlns="http://www.w3.org/1999/xhtml"><path d="M0 0"></path></svg>',
        (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink"></svg>'
        ),
        '<svg xmlns="http://www.w3.org/2000/svg"><g stroke-opacity="-0.1"></g></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><g stroke-opacity="1.01"></g></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><path fill-opacity="NaN"></path></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg"><path fill-opacity="50%"></path></svg>',
        '<svg xmlns="http://www.w3.org/2000/svg" onload="steal()"></svg>',
        (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<path href="https://attacker.test/payload" d="M0 0"></path></svg>'
        ),
        (
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<path xlink:href="data:text/html,active" d="M0 0"></path></svg>'
        ),
    ],
)
def test_inline_svg_rejects_invalid_namespace_opacity_and_active_attrs(
    hostile_svg: str,
) -> None:
    with pytest.raises(renderer_isolation.RendererPolicyError):
        renderer_isolation.admit_authored_html(hostile_svg)


@pytest.mark.parametrize(
    "malformed",
    ["<main><p>unclosed</main>", "<main><p>unclosed", "</main>"],
)
def test_unbalanced_html_fails_closed(malformed: str) -> None:
    with pytest.raises(renderer_isolation.RendererPolicyError, match="unbalanced|unclosed"):
        renderer_isolation.admit_authored_html(malformed)


def test_04_hostile_corpus_is_inert_or_rejected_by_product_admission() -> None:
    root = (
        pathlib.Path(__file__).resolve().parents[4]
        / "docs"
        / "experiments"
        / "authored-vnext"
        / "04-renderer-security"
        / "corpus"
        / "cases"
    )
    if not root.is_dir():
        pytest.skip("04 renderer-security corpus is not present")
    safe = renderer_isolation.admit_authored_html(
        (root / "00-safe-control.html").read_text(encoding="utf-8")
    )
    assert safe.source_digest.startswith("sha256:")
    for case in sorted(root.glob("*.html")):
        if case.name in {"00-safe-control.html", "09-oversized-dom.html"}:
            continue
        with pytest.raises(renderer_isolation.RendererPolicyError):
            renderer_isolation.admit_authored_html(case.read_text(encoding="utf-8"))
    # The compact experimental DOM fixture is below the product limit.  Exercise
    # its exact attack class at the real product threshold without running Chrome.
    oversized = "<main>" + "<span>x</span>" * (renderer_isolation.MAX_DOM_NODES + 1) + "</main>"
    with pytest.raises(renderer_isolation.RendererPolicyError, match="node limit"):
        renderer_isolation.admit_authored_html(oversized)


def test_raster_asset_digest_and_pixel_limits_are_enforced(monkeypatch) -> None:
    pixel = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(pixel, format="PNG")
    encoded = base64.b64encode(pixel.getvalue()).decode("ascii")
    admitted = renderer_isolation.admit_authored_html(
        f'<img alt="safe" src="data:image/png;base64,{encoded}">'
    )
    assert admitted.asset_digests == (
        "sha256:" + __import__("hashlib").sha256(pixel.getvalue()).hexdigest(),
    )
    monkeypatch.setattr(renderer_isolation, "MAX_IMAGE_PIXELS", 1)
    with pytest.raises(renderer_isolation.RendererPolicyError, match="pixel"):
        renderer_isolation.admit_authored_html(
            f'<img alt="large" src="data:image/png;base64,{encoded}">'
        )


def test_worker_environment_drops_provider_keys_and_app_mounts() -> None:
    environment = renderer_isolation.build_worker_environment(
        {
            "PATH": "safe-bin",
            "OPENAI_API_KEY": "secret",
            "ANTHROPIC_TOKEN": "secret",
            "APP_DATA_DIRECTORY": "/host/app-data",
            "USER_CONFIG_PATH": "/host/config.json",
            "CHROME_PATH": "/runtime/chrome",
        }
    )
    assert environment == {
        "PATH": "safe-bin",
        "CHROME_PATH": "/runtime/chrome",
        "AUTHORED_RENDERER_WORKER": "1",
        "AUTHORED_RENDERER_MODE": "isolated",
    }


def test_posix_chrome_spawn_receives_only_scrubbed_renderer_environment(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_spawn(path, argv, environment, *, file_actions, setsid):
        captured.update(
            path=path,
            argv=argv,
            environment=dict(environment),
            file_actions=file_actions,
            setsid=setsid,
        )
        return 4242

    monkeypatch.setenv("PATH", "safe-bin")
    monkeypatch.setenv("CHROME_PATH", "/runtime/chrome")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-chrome")
    monkeypatch.setenv("ANTHROPIC_TOKEN", "must-not-reach-chrome")
    monkeypatch.setenv("APP_DATA_DIRECTORY", "/host/app-data")
    monkeypatch.setattr(slide_capture.os, "posix_spawn", fake_spawn, raising=False)

    process = slide_capture._spawn_chrome_pipe_posix(
        "/runtime/chrome", ["/runtime/chrome", "--remote-debugging-pipe"], []
    )

    assert process.pid == 4242
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["PATH"] == "safe-bin"
    assert environment["CHROME_PATH"] == "/runtime/chrome"
    assert environment["AUTHORED_RENDERER_WORKER"] == "1"
    assert environment["AUTHORED_RENDERER_MODE"] == "isolated"
    assert "OPENAI_API_KEY" not in environment
    assert "ANTHROPIC_TOKEN" not in environment
    assert "APP_DATA_DIRECTORY" not in environment
    assert captured["setsid"] is True


def test_production_worker_command_is_json_argv_absolute_and_required(monkeypatch) -> None:
    monkeypatch.setenv("TEMPLATE_V2_DEPLOYMENT_TIER", "production")
    monkeypatch.setenv(
        "AUTHORED_RENDERER_OS_ISOLATION", "nonroot-readonly-tmpfs-network-none"
    )
    monkeypatch.delenv("AUTHORED_RENDERER_WORKER_COMMAND_JSON", raising=False)
    with pytest.raises(renderer_isolation.RendererPolicyError, match="requires"):
        renderer_isolation.worker_command()
    monkeypatch.setenv("AUTHORED_RENDERER_WORKER_COMMAND_JSON", '["python","-m","worker"]')
    with pytest.raises(renderer_isolation.RendererPolicyError, match="absolute"):
        renderer_isolation.worker_command()
    executable = pathlib.Path(sys.executable).resolve()
    launcher = (
        pathlib.Path(__file__).resolve().parents[4]
        / "contracts"
        / "authored-renderer"
        / "invoke.py"
    )
    monkeypatch.setenv(
        "AUTHORED_RENDERER_WORKER_COMMAND_JSON",
        json.dumps([str(executable), str(launcher)]),
    )
    assert renderer_isolation.worker_command()[0] == str(executable)

    monkeypatch.setenv(
        "AUTHORED_RENDERER_WORKER_COMMAND_JSON",
        json.dumps([str(pathlib.Path("C:/Windows/System32/cmd.exe")), "/c"]),
    )
    with pytest.raises(renderer_isolation.RendererPolicyError, match="invoke.py"):
        renderer_isolation.worker_command()


def test_production_worker_requires_exact_os_isolation_attestation(monkeypatch) -> None:
    monkeypatch.setenv("TEMPLATE_V2_DEPLOYMENT_TIER", "production")
    monkeypatch.setenv("AUTHORED_RENDERER_OS_ISOLATION", "proxy-only")
    monkeypatch.setenv(
        "AUTHORED_RENDERER_WORKER_COMMAND_JSON", json.dumps([str(pathlib.Path(sys.executable).resolve())])
    )
    with pytest.raises(renderer_isolation.RendererPolicyError, match="network-none attestation"):
        renderer_isolation.worker_command()


def test_legacy_requires_double_opt_in_and_is_not_an_automatic_fallback(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_RENDERER_MODE", "legacy")
    monkeypatch.delenv("AUTHORED_RENDERER_LEGACY_ENABLED", raising=False)
    with pytest.raises(renderer_isolation.RendererPolicyError, match="requires"):
        renderer_isolation.renderer_mode()
    monkeypatch.setenv("AUTHORED_RENDERER_LEGACY_ENABLED", "true")
    assert renderer_isolation.renderer_mode() == "legacy"


def test_kill_switch_prevents_cache_hit_from_returning_artifact(monkeypatch) -> None:
    slide_capture._render_cache["not-used"] = b"stale"
    monkeypatch.setenv("AUTHORED_RENDERER_KILL_SWITCH", "true")
    with pytest.raises(renderer_isolation.RendererDisabledError):
        asyncio.run(slide_capture.render_html_to_png(SAFE_HTML))


def test_kill_switch_does_not_publish_deck_placeholders(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_RENDERER_KILL_SWITCH", "true")
    with pytest.raises(renderer_isolation.RendererDisabledError):
        asyncio.run(slide_capture.render_html_list_to_pngs([SAFE_HTML]))


def test_isolated_failure_never_calls_legacy_renderer(monkeypatch) -> None:
    calls = {"legacy": 0}

    async def fail_worker(html: str, timeout: int):  # noqa: ASYNC109 - stub mirrors API
        raise slide_capture.RendererIsolationError("worker unavailable")

    async def legacy(html: str, timeout: int = 60):  # noqa: ASYNC109 - stub mirrors API
        calls["legacy"] += 1
        return _png()

    monkeypatch.setenv("AUTHORED_RENDERER_MODE", "isolated")
    monkeypatch.setenv("AUTHORED_RENDERER_LEGACY_ENABLED", "true")
    monkeypatch.setattr(slide_capture, "_render_html_in_isolated_worker", fail_worker)
    monkeypatch.setattr(slide_capture, "_render_html_to_png_subprocess", legacy)
    with pytest.raises(slide_capture.RendererIsolationError):
        asyncio.run(slide_capture._render_html_to_png_uncached(SAFE_HTML))
    assert calls["legacy"] == 0


def test_local_product_canary_blocks_host_read_and_loopback_before_browser(
    monkeypatch, tmp_path
) -> None:
    canary = tmp_path / "host-read-canary.png"
    canary.write_bytes(_png())
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.05)
    port = listener.getsockname()[1]
    calls = {"worker": 0, "legacy": 0}

    async def worker(html: str, timeout: int):  # noqa: ASYNC109 - stub mirrors API
        calls["worker"] += 1
        return _png(), None

    async def legacy(html: str, timeout: int = 60):  # noqa: ASYNC109 - stub mirrors API
        calls["legacy"] += 1
        return _png()

    monkeypatch.setenv("AUTHORED_RENDERER_MODE", "isolated")
    monkeypatch.setenv("AUTHORED_RENDERER_LEGACY_ENABLED", "true")
    monkeypatch.setattr(slide_capture, "_render_html_in_isolated_worker", worker)
    monkeypatch.setattr(slide_capture, "_render_html_to_png_subprocess", legacy)
    payloads = (
        f'<img alt="host" src="{canary.resolve().as_uri()}">',
        f'<img alt="loopback" src="http://127.0.0.1:{port}/canary.png">',
    )
    try:
        for payload in payloads:
            with pytest.raises(renderer_isolation.RendererPolicyError):
                asyncio.run(slide_capture._render_html_to_png_uncached(payload, timeout=5))
        with pytest.raises((TimeoutError, socket.timeout)):
            listener.accept()
    finally:
        listener.close()
    assert calls == {"worker": 0, "legacy": 0}


def test_isolation_failure_does_not_publish_placeholder_over_lkg(monkeypatch) -> None:
    async def fail(html: str, timeout: int = 60):  # noqa: ASYNC109 - stub mirrors API
        raise slide_capture.RendererIsolationError("boundary failed")

    monkeypatch.setattr(slide_capture, "render_html_to_png", fail)
    with pytest.raises(slide_capture.RendererIsolationError):
        asyncio.run(slide_capture.render_html_list_to_pngs([SAFE_HTML]))


def test_worker_response_is_exact_versioned_png_contract() -> None:
    png = _png()
    digest = "sha256:" + "a" * 64
    response = {
        "schema_version": "authored-render-response/v1",
        "ok": True,
        "png_base64": base64.b64encode(png).decode("ascii"),
        "metrics": None,
        "source_digest": digest,
        "asset_digests": [],
    }
    decoded, metrics = slide_capture._decode_worker_response(json.dumps(response).encode())
    assert decoded == png
    assert metrics is None
    response["unexpected"] = True
    with pytest.raises(slide_capture.RendererIsolationError, match="invalid fields"):
        slide_capture._decode_worker_response(json.dumps(response).encode())


def test_default_chrome_path_contains_pipe_data_document_and_no_unsafe_flags(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakePipe:
        def __init__(self, chrome: str, profile: pathlib.Path) -> None:
            captured["chrome"] = chrome
            captured["profile"] = profile

        def command(self, command_id, method, params=None, session_id=None):
            if method == "Target.createTarget":
                return {"targetId": "page"}
            if method == "Target.attachToTarget":
                return {"sessionId": "session"}
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "frame"}}}
            if method == "Runtime.evaluate":
                return {"result": {"value": None}}
            if method == "Page.captureScreenshot":
                return {"data": base64.b64encode(_png()).decode("ascii")}
            if method == "Page.setDocumentContent":
                captured["document"] = params["html"]
            if method == "Network.setBlockedURLs":
                captured["blocked"] = params["urls"]
            return {}

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(slide_capture, "find_chrome", lambda: "synthetic-chrome")
    monkeypatch.setattr(slide_capture, "_ChromePipe", FakePipe)
    admitted = renderer_isolation.admit_authored_html(SAFE_HTML)
    png, _metrics = slide_capture._render_admitted_html_over_pipe(admitted, 5)
    assert png.startswith(b"\x89PNG")
    assert captured["blocked"] == slide_capture._BLOCKED_RENDERER_URL_PATTERNS
    assert "Content-Security-Policy" in str(captured["document"])
    assert captured["closed"] is True


def test_source_has_no_unsafe_chrome_options_or_default_file_input() -> None:
    source = pathlib.Path(slide_capture.__file__).read_text(encoding="utf-8")
    pipe_source = pathlib.Path(slide_capture_pipe.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "--no-sandbox",
        "--disable-web-security",
        "--allow-file-access-from-files",
        "--remote-allow-origins=*",
    ):
        assert forbidden not in source
    default_path = pipe_source[pipe_source.index("class _ChromePipe") :]
    assert "--remote-debugging-pipe" in default_path
    assert '"data:,"' in default_path
    assert ".as_uri()" not in default_path


def test_static_output_profile_does_not_widen_public_admission() -> None:
    source = """<section data-slidespec="1.1"
      data-render-input-hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      style="pointer-events:none"><p data-qid="safe">safe</p></section>"""
    with pytest.raises(renderer_isolation.RendererPolicyError):
        renderer_isolation.admit_authored_html(source)
    admitted = renderer_isolation.admit_static_output_html(source)
    assert admitted.source_digest.startswith("sha256:")


@pytest.mark.parametrize(
    "source",
    [
        '<div style="pointer-events:url(https://evil.invalid/x)">x</div>',
        '<div style="mask-image:url(file:///etc/passwd)">x</div>',
        '<div data-render-input-hash="sha256:not-a-digest">x</div>',
        '<div style="background:url(data:image/svg+xml,%3Csvg%20xmlns=\'http://www.w3.org/2000/svg\'%3E%3Cscript/%3E%3C/svg%3E)">x</div>',
    ],
)
def test_static_output_profile_rejects_active_or_unbound_extensions(source: str) -> None:
    with pytest.raises(renderer_isolation.RendererPolicyError):
        renderer_isolation.admit_static_output_html(source)


def test_internal_probe_capability_is_source_bound_and_one_shot() -> None:
    from utils.renderer_internal_capability import issue_internal_render_capability

    source = '<main>safe</main><script data-codex-dom-probe>void 0</script>'
    token = issue_internal_render_capability(source)
    admitted = renderer_isolation.admit_internal_instrumented_html(source, token)
    assert f'nonce="{admitted.nonce}"' in admitted.html
    with pytest.raises(renderer_isolation.RendererPolicyError, match="already consumed"):
        renderer_isolation.admit_internal_instrumented_html(source, token)
    other = source.replace("safe", "tampered")
    token = issue_internal_render_capability(source)
    with pytest.raises(renderer_isolation.RendererPolicyError, match="invalid"):
        renderer_isolation.admit_internal_instrumented_html(other, token)


def test_outer_worker_watchdog_allows_launcher_cleanup_and_kills_posix_group() -> None:
    source = pathlib.Path(slide_capture_worker_client.__file__).read_text(encoding="utf-8")
    worker = source[source.index("async def _render_html_in_isolated_worker") :]
    assert "timeout + 25" in worker
    assert "os.killpg(process.pid, signal.SIGTERM)" in worker
    assert "os.killpg(process.pid, signal.SIGKILL)" in worker
    assert "await asyncio.wait_for(process.wait(), timeout=12)" in worker
    assert "asyncio.CancelledError" in worker
    assert "await terminate_process_tree()" in worker
