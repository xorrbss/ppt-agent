# Authored infographic measurement loop (2026-09-03)

Stage 6 of the generation-quality programme recorded in
`output/infographic-experiment-20260902/NEXT-SESSION-PROMPT.md`. Stages 1–3
decide and draw the figures; this stage is how we find out whether they work.

Three evidence surfaces, one question each:

| Surface | Question |
|---|---|
| Per-slide quality receipt | What did the pipeline choose, and how did export score it? |
| `authored_slide_editor_events` | What did a human then do about it? |
| 50-slide benchmark | Did this library change help, on fixed input? |

## 1. Per-slide quality receipts

| Module | Role |
|---|---|
| `services/authored_slide_quality_receipt.py` | The sidecar: build, write, merge, read. |
| `services/authored_presentation_generation.py` | Writes archetype/density/weight/node count after publication. |
| `services/presentation_export_direct_attempt_service.py` | Merges per-slide raster-fallback counts at export completion. |

**Why a sidecar and not a wider QualityReceipt v2.** QualityReceipt v2 is a
sealed release verdict: `_receipt_digest` is recomputed on every load and the
manifest reader raises on any shape it does not recognise. Adding measurement
fields to it would break that digest for every historical receipt and turn a
metric into a publication blocker. The generation timing receipt already
established the alternative — a sibling file in the same durable directory
(`<stem>-timing.json`, `<stem>-attempt-timing.json`) — and this follows it:

```
<app data>/authored-quality-receipts/deck-<sha256(presentation id)[:32]>-slide-quality.json
```

The existing receipt shape is therefore untouched and byte-identical.

**Keyed by presentation, not job.** Two stages write it and they do not share a
generation job: the local synchronous route runs with `generation_job=None`
(that is the only route admitted on the SQLite runtime profile, so it is the
one the benchmark uses), and export completion runs long after the job is gone.
The presentation id is the identity both stages hold.

**Shape** (`authored-slide-quality-manifest/v1`), one record per slide index:
`archetype`, `density`, `weight`, `nodeCount`, `variantChosen`, `criticScore`,
`fallbackCount`.

**`variantChosen` and `criticScore` are `null` today.** The best-of-N and
design-critic stage (Stage 4) landed in parallel with this one but does not yet
hand its per-slide result to any persister:
`services/authored_quality_loop_stage.py:apply_slide_quality_loop` computes
`outcome.receipt` — which already carries `variant_chosen` and a per-candidate
`score` (`utils/authored_figure_variants/selection.py:FigureSelection.to_dict`)
— and then returns only the winning HTML. The receipt records both fields as
*absent*, and the report and the benchmark both count absent as "not measured"
rather than as zero.

**Handover, one place.** `build_slide_quality_records` already accepts
`variants={slide_index: variant_id}` and `critic_scores={slide_index: score}`.
Whoever gives the pipeline a per-slide channel for `outcome.receipt` passes the
two mappings at the call site in
`services/authored_presentation_generation.py` and nothing else changes. A
contextvar is not that channel: per-slide authoring runs in child tasks whose
context copies do not propagate writes back.

**Merging is field-wise.** A later writer that knows only `fallbackCount` never
erases the archetype generation recorded. Every writer fails open — a
measurement file must not be why a deck fails to ship — and a manifest whose
digest does not match is discarded rather than repaired.

## 2. Editor events

One append-only table, `authored_slide_editor_events`
(`models/sql/authored_slide_editor_event.py`, migration `f2b6d8a0c4e7`):

| Column | Note |
|---|---|
| `event_type` | closed vocabulary, check-constrained: `slide_regenerated`, `archetype_switched`, `bulk_text_edit`, `slide_deleted`, `export_completed` |
| `slide_index` | NULL only for `export_completed`, the one deck-scoped event |
| `archetype` / `previous_archetype` | what the event was about; both required for a switch |
| `changed_characters` | the size of a bulk edit, never its content |
| `event_key_hash` | SHA-256 of the caller's idempotency identity, unique |

The row is scalar by design: no HTML, no prompt, no edited text.

Owner scoping is central. The model joins `_STRICT_OWNER_MODELS` in
`services/database.py`, so the `do_orm_execute` loader-criteria hook narrows
every SELECT and the `before_flush` stamper fills `owner_id`. No call site
filters by owner.

Write path: `POST /api/v1/ppt/presentation/{id}/editor-events`
(`api/v1/ppt/endpoints/authored_editor_events.py`,
`services/authored_slide_editor_event_service.py`). It resolves the deck
through `require_live_presentation`, so an event cannot be recorded against a
deck the caller may not read; `SessionAuthMiddleware` already protects every
`/api/` path. A repeated `event_key` returns the existing row with
`deduplicated: true` — a double-clicked button must not inflate an archetype's
edit rate.

**The editor does not emit these yet.** The frontend is out of scope for this
stage; the table and the write path are in place for it.

## 3. Weekly report

`servers/fastapi/scripts/authored_infographic_weekly_report.py` joins the two
surfaces on the archetype and prints counts, fallbacks per slide, slides over
the two-fallback budget, critic scores, and edit/regeneration rates.

```sh
cd servers/fastapi
APP_DATA_DIRECTORY=<app data> ./.venv/Scripts/python.exe \
  scripts/authored_infographic_weekly_report.py --days 7 [--json out.json]
```

`--receipts-dir` overrides discovery, `--no-database` reports receipts alone,
`--days 0` reads all history. It is read-only. It is Python rather than a
sibling of `results-summary.mjs` because half its input is the database, and
reaching that through the repo's own SQLAlchemy layer is cheaper and safer than
re-implementing the connection and the owner scoping in Node.

## 4. Fixed 50-slide benchmark

`scripts/benchmarks/authored-infographic/` — fixture, runner, tests and the
run recipe. See its README for why it lives there and how to point it at an
isolated backend. The fixture exercises all twenty archetypes with a single
coherent Korean proposal deck and is fixed: a change to its content is a new
fixture version, not an edit.

## Known gaps

- `variantChosen` / `criticScore` await Stage 4 (above).
- `fallbackCount` is recorded only by the direct hybrid export attempt path;
  a deck exported through another adapter leaves it `null`.
- No env knob was added, so `constants/settings_registry.json` is unchanged.
  The receipt directory continues to honour the pre-existing, unregistered
  `AUTHORED_QUALITY_RECEIPT_DIRECTORY` that `quality_receipt_root()` reads.
