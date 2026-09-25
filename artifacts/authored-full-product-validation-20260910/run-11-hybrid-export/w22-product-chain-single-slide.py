"""Run source slide 20 through the isolated Hybrid worker as a one-slide job."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time


ROOT = pathlib.Path(__file__).resolve().parent
REPRO = ROOT / "w14-direct-repro"
OUTPUT = ROOT / "w22-single-slide-product.pptx"
SCRATCH = ROOT / "w22-single-slide-product-scratch"
ALLOWLIST = (
    "COMSPEC", "LANG", "LANGUAGE", "LC_ALL", "LOCALAPPDATA", "PATH", "PATHEXT",
    "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "WINDIR",
    "AUTHORED_HYBRID_CHROME_PATH", "CHROME_PATH", "CHROMIUM_PATH",
    "PUPPETEER_EXECUTABLE_PATH",
)


def main() -> None:
    payload = json.loads((REPRO / "job-deck.json").read_text(encoding="utf8"))
    source_slide = dict(payload["slides"][19])
    source_slide["slideIndex"] = 0
    semantic_slide = dict(payload["semanticSlides"][19])
    semantic_slide["slideIndex"] = 0
    payload.update({
        "slides": [source_slide],
        "semanticSlides": [semantic_slide],
        "fontEmbedding": True,
        "concurrency": 1,
        "budgetMillis": 120_000,
        "outputPath": str(OUTPUT),
        "scratchDirectory": str(SCRATCH),
    })
    SCRATCH.mkdir(parents=True, exist_ok=True)
    command = json.loads((REPRO / "command-w20.json").read_text(encoding="utf8"))
    env = {name: os.environ[name] for name in ALLOWLIST if os.environ.get(name, "").strip()}
    env.update({
        "AUTHORED_VNEXT_RENDERER_ENABLED": "true",
        "AUTHORED_RENDERER_KILL_SWITCH": "false",
        "DISABLE_GOOGLE_FONTS_COLLECTION": "true",
        "NODE_ENV": "development",
    })
    started = time.monotonic()
    process = subprocess.run(
        command,
        cwd=str(SCRATCH),
        env=env,
        input=json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    elapsed = round(time.monotonic() - started, 3)
    (ROOT / "w22-single-slide-product.stderr.log").write_bytes(process.stderr)
    result_line = process.stdout.decode("utf8", "replace").strip().splitlines()[-1]
    result = json.loads(result_line)
    evidence = {
        "schemaVersion": "run-11-authored-hybrid-w22-single-slide/v1",
        "sourceSlideNumber": 20,
        "wireSlideNumber": 1,
        "elementId": "q-phone-step-1",
        "fontEmbedding": True,
        "elapsedMillis": round(elapsed * 1000),
        "exitCode": process.returncode,
        "result": result,
        "outputBytes": OUTPUT.stat().st_size if OUTPUT.exists() else None,
    }
    (ROOT / "w22-single-slide-product-result.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf8"
    )
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    if process.returncode != 0 or not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
