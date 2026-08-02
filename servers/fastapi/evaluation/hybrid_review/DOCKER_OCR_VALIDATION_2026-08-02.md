# Docker OCR validation — 2026-08-02

## Result

The Docker packaging contract is correct by static inspection, and the same
Tesseract binary/data contract passes a live Windows smoke test. This machine
does **not** have Docker, Docker Desktop, Podman, nerdctl, or buildctl installed,
so an image build, in-container execution, image size, and build duration could
not be measured here. They are intentionally recorded as unmeasured rather than
estimated.

## Packaging contract inspected

Both `Dockerfile` and `Dockerfile.dev` now provide the authored OCR runtime:

| Requirement | Production image | Development image |
| --- | --- | --- |
| Tesseract executable | `tesseract-ocr` when `INSTALL_TESSERACT=true` (default) | `tesseract-ocr` |
| English system language data | `tesseract-ocr-eng` | `tesseract-ocr-eng` |
| Bundled offline data | `COPY tessdata /app/tessdata` | `COPY tessdata /app/tessdata` |
| Korean bundled data | `/app/tessdata/kor.traineddata` | `/app/tessdata/kor.traineddata` |
| Authored OCR lookup | `AUTHORED_OCR_TESSDATA_PATH=/app/tessdata` | same |
| Korean render font for smoke and slides | `fonts-noto-cjk` | `fonts-noto-cjk` |

The authored OCR runtime passes `--tessdata-dir` explicitly, so it uses the
bundled data rather than relying on the OS default. `tessdata/eng.traineddata`
and `tessdata/kor.traineddata` are tracked and not excluded by `.dockerignore`.

`INSTALL_TESSERACT=false` is an intentional minimal-image option, but it makes
the native authored OCR executable unavailable. Deployments that enable authored
OCR must keep it `true` (the default); the review pipeline will fail closed for
OCR-required decisions if it is disabled.

## Live host equivalent

The following was run from the repository root with the same environment paths
the containers set, using Tesseract 5.4.0.20240606:

```powershell
$env:AUTHORED_OCR_TESSDATA_PATH=(Resolve-Path tessdata)
$env:TESSERACT_CMD='C:\Program Files\Tesseract-OCR\tesseract.exe'
Push-Location servers/fastapi
uv run pytest tests/integration/test_authored_ocr_live_smoke.py -q
```

Result: `1 passed in 0.46s` (`1.316s` measured shell wall time). The test renders
`가격 2026 +12%`, invokes `kor+eng`, checks the engine executes, requires CER
`<= 0.10`, and requires no Korean, number, or symbol omissions. A direct language
listing against the bundled directory reported exactly `eng` and `kor`.

## Required in-container verification

Run this on a Docker-capable host. It builds the normal production target with
the default native OCR option, records local image size, and runs the live smoke
inside the image. The repository bind mount is deliberately avoided so the
container's own `/app/tessdata`, language data, executable, and Noto CJK font
are tested.

```bash
docker build --progress=plain --build-arg INSTALL_TESSERACT=true \\
  -t ppt-agent-ocr-validate:20260802 .
docker image inspect ppt-agent-ocr-validate:20260802 \\
  --format '{{.Size}} bytes'
docker run --rm --entrypoint sh ppt-agent-ocr-validate:20260802 -lc '\
  tesseract --version | head -n1 && \\
  test -s /app/tessdata/eng.traineddata && \\
  test -s /app/tessdata/kor.traineddata && \\
  tesseract --tessdata-dir /app/tessdata --list-langs && \\
  cd /app/servers/fastapi && \\
  AUTHORED_OCR_TESSDATA_PATH=/app/tessdata \\
  python evaluation/hybrid_review/DOCKER_OCR_SMOKE.py'
```

`DOCKER_OCR_SMOKE.py` is copied with the FastAPI source and needs no dev-only
pytest or `uv` executable in the runtime image. It renders `가격 2026 +12%` with
the installed Noto CJK font, invokes the authored OCR utility with `kor+eng`,
and fails if CER exceeds `0.10` or Korean, numeric, or symbol content is missing.
The script prints only runtime metadata and aggregate metrics—not recognized OCR
text.

## Docker availability check

On this workstation, `docker` was absent from `PATH`; standard Docker Desktop,
Rancher Desktop, Podman, nerdctl, and buildctl locations/commands were also not
present. No image was built and no container was created or removed.
