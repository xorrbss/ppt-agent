# Docker OCR validation — 2026-08-02

## Result

The production Docker contract was checked on a real Docker Engine, but an
unmounted production-container OCR smoke is **blocked** on this workstation:
the installed Docker CLI has no Buildx component and this Dockerfile requires
BuildKit cache mounts. No image was produced, so image size, container startup,
and in-container OCR results are deliberately unmeasured.

This is not an OCR test failure. The build reached the first BuildKit-only
instruction after sending a 282.5 MB context, then failed before dependency
installation or the runtime stage.

## Actual environment and measurements

| Check | Result |
| --- | --- |
| Docker Engine (WSL Ubuntu, root) | client 29.1.3; server 29.1.3 |
| Normal production build | failed at Dockerfile step 6 because legacy builder does not support `RUN --mount` |
| BuildKit-enabled build | failed immediately: BuildKit is enabled but the Buildx component is missing or broken |
| Sent Docker context | 282.5 MB before the legacy-builder failure |
| Compose syntax/config expansion | passed (`docker compose config --quiet`); only unset optional-provider variable warnings were emitted |
| Live host OCR equivalent | `1 passed in 0.50s` (1.7 s shell wall time) |

The normal command and exact failure are:

```bash
cd /mnt/c/project/PPT-agent/ppt-agent
docker build -t ppt-agent-ocr-validate:20260802 .
# ... Sending build context to Docker daemon 282.5MB
# Step 6/51: RUN --mount=type=cache,target=/root/.cache/uv ...
# the --mount option requires BuildKit

DOCKER_BUILDKIT=1 docker build -t ppt-agent-ocr-validate:20260802 .
# ERROR: BuildKit is enabled but the buildx component is missing or broken.
```

The host `ibiz` account is not in Docker's group; invoking the WSL engine as
root was sufficient for these read/build checks. No credentials were displayed
or passed to Docker.

## Packaging contract checked

Both `Dockerfile` and `Dockerfile.dev` contain all of the following:

| Requirement | Production image | Development image |
| --- | --- | --- |
| Tesseract executable | `tesseract-ocr` when `INSTALL_TESSERACT=true` (default) | `tesseract-ocr` |
| English system language data | `tesseract-ocr-eng` | `tesseract-ocr-eng` |
| Bundled offline data | `COPY tessdata /app/tessdata` | `COPY tessdata /app/tessdata` |
| Korean bundled data | `/app/tessdata/kor.traineddata` | `/app/tessdata/kor.traineddata` |
| OCR data lookup | `AUTHORED_OCR_TESSDATA_PATH=/app/tessdata` | same |
| Korean render font | `fonts-noto-cjk` | `fonts-noto-cjk` |

`tessdata/eng.traineddata` and `tessdata/kor.traineddata` are tracked and not
ignored. The review path supplies `--tessdata-dir` explicitly, so it uses the
bundled files rather than a system-default data directory. `INSTALL_TESSERACT=false`
remains an intentional minimal-image option, but disables native authored OCR;
deployments that need it must retain the default `true`.

The Docker context was also hardened to exclude local, user-owned test and
Codex artifacts (`.test-runtime` and `.codex-tmp`) while retaining `tessdata`.
This removed a 3,018,917,297-byte test-runtime directory from the candidate
context and prevents local artifacts entering build layers.

## Live host equivalent

The following was run from the repository root against the tracked bundled
`eng` and `kor` data. It renders representative Korean/English/numeric/symbol
content, invokes `kor+eng`, requires CER <= 0.10, and rejects missing Korean,
numeric, or symbol content.

```powershell
$env:AUTHORED_OCR_TESSDATA_PATH = (Resolve-Path tessdata).Path
$env:TESSERACT_CMD = 'C:\Program Files\Tesseract-OCR\tesseract.exe'
Push-Location servers/fastapi
uv run pytest tests/integration/test_authored_ocr_live_smoke.py -q
```

Result: `1 passed in 0.50s` (1.7 s measured shell wall time). This is evidence
for the same executable/data contract only; it is not substituted for the
blocked production-container smoke.

## Required completion command

Install or repair the Docker Buildx CLI plugin, then run this from a
Docker-capable shell. The run intentionally has no repository bind mount, so it
tests the image's own `/app/tessdata`, executable, language files, and Noto CJK
font.

```bash
docker buildx version
docker build --build-arg INSTALL_TESSERACT=true \
  -t ppt-agent-ocr-validate:20260802 .
docker image inspect ppt-agent-ocr-validate:20260802 \
  --format '{{.Size}} bytes'
docker run --rm --entrypoint /bin/sh ppt-agent-ocr-validate:20260802 -c '
  set -eu
  tesseract --version | head -n1
  test -s /app/tessdata/eng.traineddata
  test -s /app/tessdata/kor.traineddata
  tesseract --tessdata-dir /app/tessdata --list-langs
  fc-match "Noto Sans CJK KR"
  cd /app/servers/fastapi
  AUTHORED_OCR_TESSDATA_PATH=/app/tessdata PYTHONPATH=/app/servers/fastapi \
    python evaluation/hybrid_review/DOCKER_OCR_SMOKE.py
'
```

On success, also start the image without a bind mount and capture its health or
HTTP readiness, image size, and startup wall time. Do not report those values
as passing until this command completes successfully.
