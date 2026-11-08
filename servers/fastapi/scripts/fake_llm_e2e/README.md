# Fake-LLM end-to-end harness

Runs the real backend with every LLM client replaced by a deterministic fake,
so the default **svg** pipeline can be exercised end to end without a provider
key: content brief, outline, SVG authoring, render, quality classification,
persistence, and the `export.pptx` workspace operation through the export
worker. It proves the plumbing, not content quality or provider latency.

```bash
cd servers/fastapi
scripts/fake_llm_e2e/run_backend.sh /tmp/fake-llm-app &   # backend on :8000
scripts/fake_llm_e2e/run_generation.sh 5                   # 5-slide deck + PPTX export
```

The PPTX lands in `<app-data>/public-artifacts/` (content-addressed). Set
`FAKE_OUTLINE_LONG=1` before starting the backend to return outline slides
above the density floor (no expansion call); leave it unset to exercise the
density-expansion path.

## Chart fixtures (V5 measurement)

`FAKE_LLM_CHART_FIXTURES=1` (default unset: behaviour unchanged) makes slide N
(2 <= N <= 8, never the last slide) carry the N-2th real-builder native chart
example from `tests/unit/fixtures/svg_chart_paint_native_examples_v1.json`
(column, coincident-line, stacked-column, donut, fixed-column, separator-pie,
marker-line). The outline puts the example's requested source text (including its
`chart:` line) in the slide content, and the SVG author returns the example's
canonical drawing with its `data-native-chart` declaration re-attached, which the
pipeline extracts and binds exactly as for a real model. Use 9 slides to get all
seven (1 cover, 7 charts, 1 closing); fixtures need the 1280x720 canvas.

```bash
SVG_NATIVE_CHART_INTEGRITY_V5=on FAKE_LLM_CHART_FIXTURES=1 FAKE_OUTLINE_LONG=1 \
  scripts/fake_llm_e2e/run_backend.sh /tmp/fake-charts &
scripts/fake_llm_e2e/run_generation.sh 9
```

## What is fake, and why

- **LLM clients** (`fake_llm_backend.py`): `llmai.get_client` returns
  `FakeClient`. Schema calls get a minimal valid instance, the outline gets
  numbered Korean slides, SVG author calls get a plain page with that slide's
  text, the deck-scoped infographic plan gets a figure for every content
  slide (labels copied from that slide's own lines; cover, section, agenda and
  closing pages stay quiet), and every response carries a usage block so provider
  receipts and setup accounting see a complete call.
- **Worker dispatcher**: `SubprocessDispatcher` runs the export/render worker
  command as a plain child process. The real trusted executor needs Windows
  job objects (`windows-local`), a Linux process session (`linux-local`,
  development only), or a container runtime.
  **It has no sandbox; never point this harness at real data.**
- **Render binding**: the launcher selects the owned local backend for the
  host (`linux-local` or `windows-local`); the binding fingerprints that
  host's font directories.
- **Pricing**: the provider pricing gate refuses an unpriced model, so the
  launcher registers the placeholder model at a zero rate.

## Settings a real (non-fake) run also needs

Found while building this harness. The base `docker-compose.yml` profiles carry none of them; the opt-in `multi-export` profile wires them (`docs/multi-process-compose-runbook-20260906.md` section 9):

| Setting | Without it |
|---|---|
| `PUBLIC_PROVIDER_PRICING_CATALOG` + `_REVISION` naming the selected model | every provider call fails closed; the default catalog prices only codex models |
| `PUBLIC_ARTIFACT_STORE_BACKEND` (`local-development` or `s3`) | `artifact.backend_required` during generation |
| `PUBLIC_WORKER_OBJECT_STORE_ROOT`, `PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY`, `ENVIRONMENT=local` (local store) | `artifact.local_root_required` / `..._signing_key_required` |
| `PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=windows-local` (Windows) or `linux-local` (Linux) + `PUBLIC_TRUSTED_WORKER_LOCAL_POLICY=development`, or `PUBLIC_RENDER_WORKER_IMAGE_DIGEST` + `PUBLIC_RENDER_WORKER_FONT_INVENTORY_DIGEST` (pinned deployment) | `public_render_worker_runtime_pins_required` |
| An installed trusted worker dispatcher: the embedded one starts only with `PUBLIC_V2_INTEGRATION_PROFILE=postgresql-strict` (PostgreSQL, per-process DB roles, and OS isolation) | `export.pptx` fails with `trusted worker dispatcher is unavailable` |

`POST /presentation/generate` (synchronous) cannot run the svg driver, which
needs a durable job; it now answers 422 `svg_generation_requires_async_route`.
Use `/generate/async`; `scripts/ppt-agent.mjs` does so automatically for svg.
