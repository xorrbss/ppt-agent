"""Generate one svg deck against a throwaway backend and collect its measurements.

Starts the fake_llm_e2e backend (real-provider mode unless --fake) on --port with a fresh
app-data directory, admits one deck through /generate/async, watches its cost, waits for the
chained PPTX export, stops the backend and writes <out>/summary.json (see collect_run.py).

Run from servers/fastapi with any python3 (stdlib only):

  PRESENTON_ANTHROPIC_API_KEY=... python3 scripts/svg_live_measurement/run_deck.py \
      --name smoke --body scripts/svg_live_measurement/bodies/p1.json --out /var/tmp/svg-live/smoke

The provider key is read from $PRESENTON_ANTHROPIC_API_KEY and handed to the backend process
only; it is never printed, logged or written. --fake needs no key (FAKE_LLM_CLIENTS=1).
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
FASTAPI = HERE.parent.parent
sys.path.insert(0, str(HERE))

import collect_run  # noqa: E402

PRICING_REVISION = "anthropic-55-20261102"
MODEL = "claude-sonnet-5-5"
PRICING_CATALOG = json.dumps({
    "schemaVersion": "public-provider-pricing-catalog/v1", "revision": PRICING_REVISION,
    "entries": [{"providerId": "anthropic", "modelId": MODEL,
                 "inputMicrounitsPerMillionTokens": 2000000, "outputMicrounitsPerMillionTokens": 10000000,
                 "cachedInputMicrounitsPerMillionTokens": 200000, "currency": "USD"}]})
CREDENTIALS = {"username": "admin", "password": "fake-llm-harness"}
TERMINAL = {"completed", "error", "failed"}


def _uptime() -> str:
    return subprocess.run(["uptime"], capture_output=True, text=True, check=False).stdout.strip()


def _apply_override(body: dict, override: dict) -> dict:
    """Shallow merge; a null value deletes the key."""
    merged = dict(body)
    for key, value in override.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


class Http:
    def __init__(self, base: str) -> None:
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, payload: dict | None = None, timeout: float = 60):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"content-type": "application/json"})
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError):
            return 0, ""


def _port_busy(port: int) -> bool:
    return Http(f"http://127.0.0.1:{port}").call("GET", "/api/v1/auth/status", timeout=3)[0] != 0


def start_backend(args, app: Path, out: Path) -> subprocess.Popen:
    env = dict(os.environ)
    # The key reaches the backend only; the generation client below never sees it.
    key = env.pop("PRESENTON_ANTHROPIC_API_KEY", "")
    for name in ("ANTHROPIC_API_KEY", *args.unset):
        env.pop(name, None)
    env["PORT"] = str(args.port)
    if args.fake:
        env["FAKE_LLM_CLIENTS"] = "1"
    else:
        if not key:
            raise SystemExit("PRESENTON_ANTHROPIC_API_KEY is not set (use --fake for a no-cost dry run)")
        env.update(FAKE_LLM_CLIENTS="0", LLM="anthropic", ANTHROPIC_MODEL=MODEL, ANTHROPIC_API_KEY=key,
                   PUBLIC_PROVIDER_PRICING_REVISION=PRICING_REVISION, PUBLIC_PROVIDER_PRICING_CATALOG=PRICING_CATALOG,
                   CANVAS_BENCH_ENABLED="true", FAKE_LLM_LOG_PROVIDER_ERRORS="1")
    env.update(args.env)
    log = open(out / "backend.log", "wb")  # noqa: SIM115 - owned by the child for its lifetime
    process = subprocess.Popen(["bash", str(FASTAPI / "scripts/fake_llm_e2e/run_backend.sh"), str(app)],
                               cwd=FASTAPI, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    (out / "backend.pid").write_text(str(process.pid))
    return process


def stop_backend(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=25)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def wait_ready(http: Http, process: subprocess.Popen, seconds: int) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline and process.poll() is None:
        if http.call("GET", "/api/v1/auth/status", timeout=3)[0] != 0:
            return True
        time.sleep(2)
    return False


def generate(http: Http, body: dict, app: Path, out: Path, timeout: int, max_cost: float | None):
    http.call("POST", "/api/v1/auth/setup", CREDENTIALS)
    http.call("POST", "/api/v1/auth/login", CREDENTIALS)
    code, text = http.call("POST", "/api/v1/ppt/presentation/generate/async", body)
    (out / "admission.json").write_text(text)
    try:
        task = json.loads(text).get("id", "")
    except ValueError:
        task = ""
    if code >= 300 or not task:
        return "admission_refused", 0.0
    start = time.time()
    state = ""
    status_text = ""
    while time.time() - start < timeout:
        _, status_text = http.call("GET", f"/api/v1/ppt/presentation/status/{task}")
        try:
            state = json.loads(status_text).get("status", "")
        except ValueError:
            state = ""
        if state in TERMINAL:
            break
        if max_cost is not None and collect_run.running_cost(app) > max_cost:
            (out / "status.json").write_text(status_text)
            return "cost_cap_abort", time.time() - start
        time.sleep(3)
    (out / "status.json").write_text(status_text)
    return (state or "timeout"), time.time() - start


def render_pngs(out: Path) -> str:
    """Slide PNGs via Playwright in a throwaway uv environment (see render_pngs.py)."""
    svg_dir = out / "svg"
    if not any(svg_dir.glob("*.svg")):
        return "no svg to render"
    version = os.environ.get("SVG_MEASURE_PLAYWRIGHT", "1.52.0")
    result = subprocess.run(["uv", "run", "--no-project", "--quiet", "--with", f"playwright=={version}", "python",
                             str(HERE / "render_pngs.py"), str(svg_dir), str(out / "png")],
                            capture_output=True, text=True, check=False, timeout=600)
    return (result.stdout + result.stderr).strip()[-400:] or "rendered"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--body", required=True, help="request body JSON file")
    parser.add_argument("--body-override", default="{}", help="JSON merged over the body; null deletes a key")
    parser.add_argument("--out", required=True, help="run directory (must not hold a finished run)")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--arm", default="")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--env", action="append", default=[], metavar="K=V", help="backend env (repeatable)")
    parser.add_argument("--unset", action="append", default=[], metavar="K", help="env removed for the backend")
    parser.add_argument("--fake", action="store_true", help="fake LLM clients: no key, no cost")
    parser.add_argument("--max-cost", type=float, default=None, help="abort the deck above this USD cost")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--export-timeout", type=int, default=900)
    parser.add_argument("--no-png", action="store_true")
    args = parser.parse_args()
    args.env = dict(item.split("=", 1) for item in args.env)

    out = Path(args.out).resolve()
    if (out / "summary.json").exists():
        print(f"skip {args.name}: {out}/summary.json exists")
        return 0
    if _port_busy(args.port):
        raise SystemExit(f"port {args.port} is busy")
    body = _apply_override(json.loads(Path(args.body).resolve().read_text()), json.loads(args.body_override))
    app = out / "app"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    (out / "body.json").write_text(json.dumps(body, ensure_ascii=False, indent=1))
    (out / "arm_env.json").write_text(json.dumps({"env": args.env, "unset": args.unset}, indent=1))

    http = Http(f"http://127.0.0.1:{args.port}")
    up_before = _uptime()
    process = None
    state, wall, export_waited = "backend_failed", 0.0, 0
    try:
        process = start_backend(args, app, out)
        if wait_ready(http, process, 240):
            state, wall = generate(http, body, app, out, args.timeout, args.max_cost)
            if state == "completed" and body.get("export_as"):
                export_waited = collect_run.wait_export(app, args.export_timeout, body["export_as"])
    finally:
        up_after = _uptime()
        stop_backend(process)
    meta = {"run": args.name, "arm": args.arm, "prompt": args.prompt, "mode": "fake" if args.fake else "real",
            "client_state": state, "wall_s_client": round(wall, 1), "export_wait_s": export_waited,
            "uptime_before": up_before, "uptime_after": up_after}
    result = collect_run.collect(out, meta)
    if not args.no_png:
        result["png"] = render_pngs(out)
        (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    shown = {k: result.get(k) for k in ("run", "arm", "client_state", "wall_s_operation", "cost_usd", "receipt_calls",
                                          "gate", "fallback_pages", "export_state", "export_s", "png")}
    print(json.dumps(shown, ensure_ascii=False))
    return 0 if state == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
