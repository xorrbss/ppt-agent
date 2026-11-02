# Measuring an svg deck end to end on the Windows local stack (2026-11-02)

How to generate one svg deck on the supervised Windows stack, time every
stage, export it to PPTX and capture slide screenshots. Written after the first
complete Linux run of the same flow (5 pages, Anthropic `claude-sonnet-5`,
results in section 6) so the two can be compared.

## 1. Preconditions

- The stack runs through `scripts\start-local-stack.ps1` (PostgreSQL, API,
  generation and export controllers, Next.js, proxy). It already uses
  `PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=windows-local` with
  `PUBLIC_TRUSTED_WORKER_LOCAL_POLICY=development`; nothing else is needed for
  the render binding or the export dispatcher.
- Provider. Codex models are priced in the default
  `PUBLIC_PROVIDER_PRICING_CATALOG`. Any other provider fails closed until its
  model is in the catalog: set `PUBLIC_PROVIDER_PRICING_CATALOG` and bump
  `PUBLIC_PROVIDER_PRICING_REVISION` with
  `pwsh scripts\set-local-stack-knob.ps1` for the `api`,
  `worker-generation` and `worker-export` targets, then restart the stack.
- Anthropic models. `claude-sonnet-4-20250514` (the registry default) is
  retired. `claude-sonnet-5-5` and `claude-opus-5-5` reject forced
  `tool_choice` (HTTP 400 on the first structured call), so use a model that
  accepts it, for example `claude-sonnet-5`.

## 2. Generate and export

The synchronous `/generate` route refuses svg (422
`svg_generation_requires_async_route`); `scripts/ppt-agent.mjs` takes the
async route automatically for svg.

```powershell
node scripts\ppt-agent.mjs --base http://localhost:5000 --user <admin> --password <password> `
  --slides 5 --export pptx --out .\out --timeout 1700 `
  "중소기업을 위한 생성형 AI 도입 전략: 현황, 기대 효과, 단계별 로드맵, 리스크 관리"
```

`--common-header free` skips the designed common header (the default is
`uniform`: two header design calls plus a reduced fallback). The CLI prints
`presentation_id`, `export op` and the downloaded path.

## 3. Stage timings

Every provider call is one row in `public_provider_logical_calls`; the
generation attempt's wall-clock stages are in the operation's timing receipt.
Run as the database owner (substitute the presentation id the CLI printed):

```sql
-- Per call: stage, page, tokens, latency. Calls are matched to the deck by
-- the generation operation's lifetime, so measure one deck at a time.
SELECT c.stage, c.slide_index, c.retry_class, c.outcome,
       c.input_tokens, c.output_tokens, round(c.latency_ms / 1000.0, 1) AS seconds,
       c.created_at
FROM public_provider_logical_calls c
JOIN workspace_operations o
  ON o.presentation_id = :'presentation_id' AND o.kind = 'presentation.generate'
 AND c.created_at BETWEEN o.created_at AND o.updated_at
ORDER BY c.created_at;

-- Attempt stages (milliseconds) and export operation timing.
SELECT kind,
       result -> 'generation_timing_receipt' -> 'stages' AS stages,
       result -> 'generation_timing_receipt' -> 'wallMillis' AS wall_ms,
       created_at, updated_at
FROM workspace_operations
WHERE presentation_id = :'presentation_id'
ORDER BY created_at;
```

The receipt's stages overlap by design: `outline` covers the content brief,
outline and header design calls; `authoring` the parallel page calls;
`repair` and `chromiumReview` the quality loop; `provider` is the union of
provider time. The export time is the `export.pptx` row's
`updated_at - created_at` (the CLI also prints it as `export_follow_ms` when
driven through `scripts/lib/ppt-agent-client.mjs`).

Author concurrency is visible directly: with the default
`SVG_AUTHOR_CONCURRENCY` (5), the `author` rows of a 5-page deck share one
`created_at` second.

## 4. Screenshots

```powershell
soffice --headless --convert-to pdf .\out\presentation-<id>-r1.pptx --outdir .\out
pdftoppm -r 96 -png .\out\presentation-<id>-r1.pdf .\out\slide
```

LibreOffice renders the exported OOXML, not the browser preview, so it is an
honest check of the PPTX itself. PowerPoint on the same machine is the
reference renderer for final judgement.

## 5. Failure triage

| Code | Meaning |
|---|---|
| `public_render_worker_runtime_pins_required` | no owned local backend and no pinned worker digests |
| `artifact.backend_required` | `PUBLIC_ARTIFACT_STORE_BACKEND` unset |
| `public_provider_bridge_exception` | provider rejected the request; the HTTP body is not stored, reproduce the call directly |
| `public_provider_empty_output` | the provider returned no text, e.g. thinking consumed the whole budget |
| `svg_common_header_*` | header design/binding; the traceback is in the generation controller log ("Bounded header failure") |

## 6. Reference: Linux run, 2026-11-02

PostgreSQL + `postgresql-strict`, API + generation + export controllers,
`PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=linux-local`, `claude-sonnet-5`,
5 pages, default `uniform` header (fell back to the reduced header).

| Stage | Seconds |
|---|---|
| content brief (initial + density retry) | 50.5 + 31.5 |
| outline | 27.4 |
| header design (2 calls) | 21.4 + 22.2 |
| page authoring (5 in parallel) | 6.7 – 71.2 (wall 71.6) |
| repair (4 pages) | 7.1 – 61.8 (wall 110.6) |
| vision QA (2 calls) | 24.9 + 23.5 |
| generation total (receipt wall) | 330.1 |
| `export.pptx` (no font embedding / with font embedding) | 10.3 / 40.8 |

Before the authored reasoning effort reached Anthropic, the same pages took
~155 s each at ~15k output tokens (mostly hidden thinking), and two of five
hit the 16k cap with no text.
