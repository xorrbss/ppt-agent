# W22 — Hybrid line-break integrity and per-slide fallback

## Outcome

The line-break refusal is fixed at its actual source, and the same 35-slide
product export now completes as `editable-hybrid`. Operation
`0bd9ff3a-6fcf-41d5-b314-33dc1736a12b` published one 7,807,484-byte artifact
with 35/35 slides carrying native layers, `slideFallbacks: []`, and no
`hybrid_degraded_reason`. The isolated worker ran once in 893,786.557 ms, below
the 1,740,000 ms grant.

## Root cause

The saved slide-20 HTML contains `q-phone-step-1` as a flex row with two text
owners: `짧게 소개` and the child arrow span `→`. Chromium measured both runs on
visual line index 0 and emitted no run with `breakKind: line` or
`breakKind: paragraph`. `innerText`, however, inserted a newline before the
blockified flex child, so the observation carried `plainText: "짧게 소개\n→"`
and two derived `paragraphs` despite having zero measured hard breaks.

`pptx-assembler.ts` previously computed the required break count as the maximum
of measured hard-break runs and `paragraphs.length - 1`. It therefore demanded
one break even though `native-plan.ts` correctly serialized the two same-line
runs into one DrawingML paragraph. The failure was an `authoredBreakCount`
over-count, not a loss in browser measurement or run serialization.

The new `pptx-text-integrity.ts` uses explicit hard-break runs as authority for
modern observations and consults derived paragraphs only for legacy
observations with no runs. The integrity check remains fail-closed: a genuine
hard break still requires a serialized `<a:br/>` or paragraph boundary. Its
typed refusal now carries `text_break_integrity`, slide number, and element ID.

## Failure scope

`public-hybrid-export-worker.mjs` now routes `text_break_integrity` through a
bounded per-slide assembly helper. It removes only the owning slide's native
layer, replaces that slide's quality record with the existing full-slide image
fallback, reassembles from the fidelity skeleton, and persists the stable code
in `slideFallbacks`. Other assembler failures still escape and downgrade the
deck; the change does not turn unknown corruption into a successful Hybrid
artifact.

## Verification

- Code commit: `f48c8a6f31` (`fix(authored-hybrid): preserve measured text breaks per slide`).
- Focused Node tests: 2/2 passed for the flex `innerText` artifact plus genuine
  hard-break preservation, and for one-slide-only `text_break_integrity`
  routing.
- `npx tsc --noEmit` in `servers/nextjs`: passed.
- Product-chain source-slide-20 harness: succeeded in 50,482 ms with one
  editable wire slide and `slideFallbacks: []`; evidence is
  `w22-single-slide-product-result.json` and `w22-single-slide-product.pptx`.
- Offline browser/assembler reproduction: the two runs share line index 0,
  derived paragraphs claim one break, measured hard breaks are zero, and the
  fixed assembler succeeds. Evidence is `w22-root-cause-repro.json`.
- Full product command: idempotency key
  `run11-hybrid-export-w22-20260911145745`; a transient proxy 502 interrupted
  polling at about 685 seconds, but the durable operation continued and the
  harness resumed the same operation without a second POST.
- Full product receipt: status `succeeded`, requested and delivered mode
  `editable-hybrid`, operation wall 925,391 ms, export worker 896,192 ms, first
  dispatch 893,786.557 ms, worker execution count 1, no deck degradation, and
  no slide fallback.
- PPTX inspection: SHA-256
  `f11eb80e3b1a436669bee356289b6c159c9f7fc38577d0d665dae63bea7beeab`
  matches the receipt; 35 slides, 35 native-layer slides, 1,384 `p:sp`, 233
  `p:pic`, and 872 `a:t` nodes. Of the established 289 source readback strings,
  137 equal a complete individual `a:t` node; this bounded metric does not join
  adjacent runs. Slide 20 records `q-phone-step-1` as `짧게 소개` and `→` in one
  paragraph with zero `<a:br/>`, matching Chromium's zero measured hard breaks.

The requested file-size ratchet command ran but the dirty shared worktree did
not pass it: it reported ten pre-existing, unrelated oversized/grown files in
FastAPI, workspace UI, `export.ts`, and `slide-layer.ts`. This change added no
oversized file and did not grow an existing offender: `pptx-assembler.ts`
shrunk from 751 to 746 lines, and the worker remains 492 lines. A broader
combined Node run also exposed one unrelated existing assertion mismatch in
the assembler suite (`hybrid-s1-backplate-*` expected while the fixture emits
`hybrid-s1-raster-band-0-*`); the two W22-focused tests pass.

## Evidence files

- `45-command-response.json`
- `46-timeline.json`
- `47-evidence.json`
- `48-hybrid-export.pptx`
- `w22-root-cause-repro.json`
- `w22-single-slide-product-result.json`
- `w22-report.md`

The full product rerun intentionally preserves the existing 35-slide authored
presentation. It does not re-run generation or change the original source
document's 43-page composition contract.
