import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def _extract_block(source: str, signature: str) -> str:
    signature_index = source.find(signature)
    assert signature_index >= 0, f"missing block: {signature}"
    opening_brace = source.find("{", signature_index + len(signature))
    assert opening_brace >= 0, f"missing opening brace: {signature}"

    depth = 0
    for index in range(opening_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[signature_index : index + 1]

    raise AssertionError(f"missing closing brace: {signature}")


def test_nginx_rejects_unauthenticated_mcp_and_forwards_credentials():
    nginx = _read("nginx.conf")
    auth_check = _extract_block(nginx, "location = /_mcp_auth_check")

    assert re.search(r"\binternal\s*;", auth_check)
    assert re.search(
        r"proxy_pass\s+http://localhost:8000/api/v1/auth/verify\s*;",
        auth_check,
    )
    assert re.search(r"proxy_pass_request_body\s+off\s*;", auth_check)
    assert re.search(r"proxy_set_header\s+Cookie\s+\$http_cookie\s*;", auth_check)
    assert re.search(
        r"proxy_set_header\s+Authorization\s+\$http_authorization\s*;",
        auth_check,
    )
    assert re.search(r'proxy_set_header\s+X-Original-URI\s+""\s*;', auth_check)

    for signature, upstream in (
        ("location = /mcp", "http://localhost:8001/mcp"),
        ("location ^~ /mcp/", "http://localhost:8001/mcp/"),
    ):
        location = _extract_block(nginx, signature)
        assert re.search(r"auth_request\s+/_mcp_auth_check\s*;", location)
        assert f"proxy_pass {upstream};" in location
        assert re.search(
            r"proxy_set_header\s+Cookie\s+\$http_cookie\s*;", location
        )
        assert re.search(
            r"proxy_set_header\s+Authorization\s+\$http_authorization\s*;",
            location,
        )

    assert not re.search(r"location\s+/mcp\s*\{", nginx)


def test_start_js_keeps_mcp_opt_in_and_production_reload_disabled():
    launcher = _read("start.js")
    assert re.search(
        r"const shouldStartMcpServer\s*=\s*"
        r'process\.env\.ENABLE_MCP_SERVER\s*===\s*"true"\s*;',
        launcher,
    )

    enabled_block = _extract_block(launcher, "if (shouldStartMcpServer)")
    assert "spawn(" in enabled_block
    assert '"mcp_server.py"' in enabled_block
    assert re.search(
        r'"--reload",\s*isDev\s*\?\s*"true"\s*:\s*"false"',
        enabled_block,
    )
    assert launcher.count('"mcp_server.py"') == 1

    enabled_end = launcher.index(enabled_block) + len(enabled_block)
    assert re.search(
        r'}\s*else\s*\{\s*console\.log\(\s*"MCP server disabled',
        launcher[enabled_end - 1 : enabled_end + 200],
    )


def test_native_fastapi_launch_paths_enable_startup_migrations():
    launcher = _read("start.js")
    electron_main = _read("electron/app/main.ts")
    electron_servers = _read("electron/app/utils/servers.ts")

    assert re.search(
        r"MIGRATE_DATABASE_ON_STARTUP:\s*"
        r'process\.env\.MIGRATE_DATABASE_ON_STARTUP\s*\?\?\s*"true"',
        launcher,
    )
    assert re.search(r"env:\s*fastApiEnvironment", launcher)
    assert re.search(r'MIGRATE_DATABASE_ON_STARTUP:\s*"True"', electron_main)
    assert re.search(
        r"env:\s*\{\s*\.\.\.process\.env,\s*\.\.\.env\s*\}",
        electron_servers,
    )
    assert re.search(
        r'server\.py",\s*"--port",\s*port\.toString\(\),\s*'
        r'"--reload",\s*"false"',
        electron_servers,
    )
