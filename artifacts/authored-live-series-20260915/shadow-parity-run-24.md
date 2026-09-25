# Shadow admission parity — run-24 (verbatim 35-slide deck)

Read-only analysis. No code, test, stack or running series was touched.

- Log: `app_data/logs/worker-generation-20260912-025947.stderr.log`
- Run-24 operation: `9d2e3246-eee9-44e2-957b-11ccacb9a601`
  (`artifacts/authored-full-product-validation-20260910/run-24/05-admission.json`)
- Gate mode: `shadow`. Rollout defaults in effect: `gate_mode="shadow"`,
  `html_normalization="legacy"`, `placeholder_authoring=False`
  (`servers/fastapi/services/authored_rollout_profile.py:74-80`).
- Log read at 1605 lines; the file is still being appended to by the live series.

## 0. Scoping correction (read this first)

The log interleaves **three** operations, not one. Line ranges, by the first and last
line carrying each `operation_id`:

| operation | log lines | outcome |
| --- | --- | --- |
| `9d2e3246…` (**run-24**) | 4–564 | failed at slide 20, `source_content_browser_readback_failed` |
| `a69552b1…` | 565–1298 | **published** (`[authored] done … slides=35`, line 1298) |
| `6b04e359…` | 1299–1605 | still running at read time |

`scripts/authored_admission_shadow_report.py` aggregates the **whole file**, so the
counts quoted in the task (author 2, author_batch 37, author_batch rejected→rejected 18,
render 5) are whole-log aggregates at an earlier read, not run-24 rows. At this read the
whole-log figures are author 2, author_batch 45, author_batch rejected→rejected 22,
render 8. The run-24-scoped table is below and is stable (run-24 ended at line 564).

## 1. Parity table

### 1a. Run-24 only (lines 4–564), 371 rows

| producer | existing verdict | existing code | gate verdict | gate code | count | mismatch |
| --- | --- | --- | --- | --- | ---: | --- |
| author | admitted | — | admitted | — | 70 | |
| author | admitted | — | rejected | `source_content_browser_readback_failed` | 2 | **yes** (G1) |
| author_batch | admitted | — | admitted | — | 104 | |
| author_batch | admitted | — | rejected | `source_content_browser_readback_failed` | 14 | **yes** (G1) |
| author_batch | admitted | — | rejected | `authored_slide_author_failed` | 1 | **yes** (G2) |
| author_batch | rejected | `authored_slide_author_failed` | rejected | `authored_slide_author_failed` | 1 | |
| author_batch | rejected | `source_content_browser_readback_failed` | rejected | `source_content_browser_readback_failed` | 6 | |
| author_rescue | admitted | — | admitted | — | 3 | |
| author_rescue | rejected | `source_content_browser_readback_failed` | rejected | `source_content_browser_readback_failed` | 1 | |
| illustration | admitted | — | admitted | — | 136 | |
| render | admitted | — | admitted | — | 31 | |
| render | admitted | — | rejected | `source_content_browser_readback_failed` | 2 | **yes** (G3) |

Mismatch line numbers (run-24):

- G1 `author_batch`: 198, 199, 217, 218, 272, 273, 341, 342, 386, 387, 441, 442, 494, 495
- G1 `author`: 559, 560
- G2 `author_batch`: 291
- G3 `render`: 269, 371

### 1b. Whole log (all three operations), 872 rows — additional group

| producer | existing verdict | existing code | gate verdict | gate code | count | where |
| --- | --- | --- | --- | --- | ---: | --- |
| publication | unobserved | — | **error** | `authored_admission_internal_error` | 1 | line 1297, op `a69552b1` |

Also present outside run-24 and already at parity: `structural_patch` 2 admitted / 3
rejected-rejected, `vqa_repair` 3 admitted / 1 rejected-rejected.

### 1c. Bounded fields: predicate and block ids are **not** in the log

`AdmissionShadow.record` emits exactly five fields — `producer`, `existingVerdict`,
`existingCode`, `gateVerdict`, `gateCode`
(`servers/fastapi/services/authored_admission_shadow.py:36-42`, logged at `:48`). The
gate's `predicate` and `block_ids` exist on the `Admission`/`AdmissionReceipt`
(`servers/fastapi/services/authored_admission.py:68-69`,
`servers/fastapi/services/authored_admission_receipt.py:48,78-80`) but are never written
to the shadow line and, for a rejected shadow admission, are never persisted either
(shadow does not reach `admit_checkpoint_write`). **Predicate and block ids below are
therefore derived from the code path and from row adjacency, not read from the log.**
This is an instrumentation gap against the design's step-1 target of "zero *unexplained*
verdict differences" (`docs/…-20260911.md` §4.1): today a difference cannot be explained
from the log alone.

## 2. Why the mismatches happen at all: the legacy comparator is not the same check

`check_producer` compares the gate against whatever callable the producer adapter passes
as `legacy` (`servers/fastapi/services/authored_admission_session.py:240`). Three adapters
pass a comparator that is **weaker than, or empty compared to, the gate**:

| entry point | `legacy` comparator | file:line |
| --- | --- | --- |
| `provider_raw_html` | `validate_policy=False`, `bindings=None` → **identity, no check at all** | `authored_admission_producers.py:37-42,59-68` |
| `provider_expanded_html` | `validate(...)` only, `bindings=None` → **no source binding** | `authored_admission_producers.py:37-42,71-79` |
| `prepare_checkpoint_write` (author/illustrate/render) | `legacy=lambda value: value` → **identity** | `authored_admission_checkpoint.py:148` |
| `author_html(bindings=…)` (`persist_batch_success`, pipeline author stage) | `validate(...)` **and** `stamp(...)` → the real check | `authored_admission_producers.py:39-41`; `authored_presentation_author_batch.py:21-25`; `authored_presentation_pipeline.py:740-745` |

The authoring path calls the gate **three times per slide attempt**, in this order:

1. `provider_raw_html` at the top of `_postprocess_slide` (`utils/llm_calls/author_batch.py:242`,
   `utils/llm_calls/author_slide.py:1198`) — representation `token`, legacy = identity;
2. `provider_expanded_html` at the bottom of `_postprocess_slide`
   (`author_batch.py:288`, `author_slide.py:1246`) — representation `expanded`, legacy = validate only;
3. `persist_batch_success` / the pipeline author stage — legacy = validate **and** bind.

That is exactly the shape every G1/G2 cluster has in the log: two adjacent mismatches
followed by one `rejected → rejected` parity row (198/199/**200**, 217/218/**219**,
272/273/**274**, 341/342/**343**, 441/442/**443**, 494/495/**496**; and 291/**292** for G2).

## 3. Per-group verdicts

### G1 — `author` / `author_batch`, `source_content_browser_readback_failed` (16 rows in run-24)

**Predicate (derived):** in the `token`/`expanded` states the gate performs no rendered
readback. The only producer of `source_content_browser_readback_failed` on that path is
the source binder reached through
`text_step → bind_current_source → stamp_source_blocks`
(`authored_admission.py:102-119,186`), i.e. `services/source_content/text_stamping.py` —
the same binder and the same `context.bindings` the legacy call at step 3 uses
(`authored_admission_producers.py:41`). The run-24 deck failure detail
("source block text has no unique DOM element (run 0: expected '꿀벌쌤')") comes from that
module, `text_stamping.py:348-352`.

**Verdict: (1) correctly stricter — the same predicate, run earlier, against a comparator
that is empty at that entry point.** Evidence:

- In every G1 cluster in run-24 where step 3 was reached, the legacy binder rejected the
  same slide with the same code (6 of 7 clusters: lines 200, 219, 274, 343, 443, 496).
  The one cluster without a step-3 row (386/387) is the slide-20 attempt that died on
  `authored_header_contract_rejected` before `persist_batch_success` — see the
  `prior_failure` in `run-24/06-final-status.json`.
- Across the **whole** log (872 rows, 3 operations) there is **not one** case of
  "raw entry rejects, expanded entry admits". Every raw-entry mismatch is paired with an
  expanded-entry mismatch of the same code. So binding raw model output before
  `_strip_nonvisual_pointer_events` / font floor / `finalize_infographic_slide_html` /
  `_bind_offered_source_image_slots` was never observed to be *additionally* strict on
  this corpus.
- Design cover: §2.3's fixed order is "raw-input diagnostics → parse + rewrite → policy →
  **substitution + static checks** → post-expansion limits → (captured only) rendered
  integrity + visibility", and the mode table makes substitution/static checks **fatal**
  for verbatim decks. Binding at the author stage is the substitution step, not an extra
  one.

**Ruled out false-rejection hypotheses, with code evidence:**

- *Placeholder context checks running with the knob off* — `check_placeholder_context` is
  guarded by `context.state == "token" and placeholder_authoring_enabled()`
  (`authored_admission.py:168-171`), and `placeholder_authoring` defaults to `False`
  (`authored_rollout_profile.py:77`). The text step is the legacy binder
  (`authored_admission.py:111-116`). Not firing.
- *Wrong representation state* — `author_html` pins `representation="token" if fresh …
  else "expanded"` (`authored_admission_producers.py:50`); `provider_raw_html` is
  `raw_input=True, fresh=True` → `token`, `provider_expanded_html` is `fresh=False` →
  `expanded`. Both match the design's state table. The captured-only branch
  (`authored_admission.py:197-216`) cannot run in either.
- *Raw diagnostics stricter than the renderer* — with `html_normalization="legacy"`,
  `raw_diagnostics_step` runs only size / NUL / reserved-marker / duplicate-attribute
  checks, and the duplicate-attribute predicate is imported from the renderer itself
  (`duplicate_attribute_counts` from `utils/renderer_isolation`,
  `authored_admission.py:24,28-59`). The tree diagnostics are behind the `tree` switch.
- *Deck cardinality with a one-slide manifest* — `readback_documents()` asserts
  `len(units) == len(slides)` on the **deck** documents before selecting one unit
  (`authored_admission_context.py:89-104`), and 31 of 33 run-24 render admissions and all
  136 illustration admissions passed, so the manifest/projection pair is well-formed.
  `inspect_browser_source_readback`'s cardinality check
  (`services/source_content/browser_readback_inspection.py:47`) sees 1 unit vs 1 metric
  and cannot fire.

**Enforce-mode behaviour of this group (no deck-death change, but a real change of
surface):** under enforce `check_producer` calls `result.require()`
(`authored_admission_session.py:233-235`), which raises
`BrowserSourceReadbackError` from *inside* `_postprocess_slide`. That call is wrapped by
the batch's per-slide validation boundary — `except BaseException … errors[index] = error`
(`utils/llm_calls/author_batch.py:438-446`) — so it becomes the same per-slide provider
failure the step-3 rejection becomes today
(`authored_presentation_author_batch.py:87-93`), against the same attempt budget. The
slide's outcome is unchanged; the failure simply arrives one step earlier and skips the
post-processing work. Two residual differences that are **undecidable offline**:
(a) the retry-feedback payload differs (the raw-entry error carries no `rejected_html`
attribute, unlike the ValueErrors raised further down `_postprocess_slide`);
(b) under enforce `author_html` returns `result.html` — the gate's *stamped* document —
into the middle of `_postprocess_slide`, so the font floor, infographic finalization and
image-slot binding would then operate on already-stamped HTML and the expanded entry
would re-bind it. That re-entrancy is not exercised in shadow and is not observable here.

### G2 — `author_batch`, `authored_slide_author_failed` (1 row in run-24, line 291)

**Predicate (derived):** `RendererPolicyError` from the gate's `rewrite_policy` step,
mapped at `authored_admission.py:219-220` to `RENDERER_POLICY_FAILED`
(`authored_admission_policy.py:11`).

**Verdict: (1) correctly stricter — same predicate, one step earlier.** The mismatch is
the raw entry, where `validate_policy=False` makes the comparator an identity
(`authored_admission_producers.py:59-68`). The very next row (292) is the expanded entry,
where the legacy `validate` raises the same code — `rejected → rejected` parity. Design
§2.3 mode table: "raw diagnostics, parse/rewrite, policy" is **fatal** in every column.

### G3 — `render`, `source_content_browser_readback_failed` (2 rows in run-24: 269, 371)

**Predicate (derived, bounded but not pinned):** `prepare_checkpoint_write` forces
`representation="captured"` for stage `render`
(`authored_admission_checkpoint.py:153`), so the rejection must come from the
captured-only branch of `admit_slide_html` — one of
"source text was not sealed before capture" (`:181-184`), `capture_binding` (`:197-201`),
"capture telemetry is unavailable" (`:202-204`), or the rendered-integrity / visibility /
telemetry verdict from `inspect_browser_source_readback` (`:205-216`,
`rendered_verdict` at `:126-150`). The per-slide render checkpoint always passes both
`png` and `browser_metrics` (`authored_presentation_pipeline.py:1383-1392`), which makes
the "capture telemetry is unavailable" case unlikely but not excluded. **Which of the
four fired is undecidable offline**: the shadow line carries no predicate, the gate's
`inspect_browser_source_readback` does not log, and run-24 contains no
`browser source readback rejected:` line at all (those appear only in op `a69552b1`,
lines 1155/1159/1161/1281, from legacy call sites).

**Verdict: (1) correctly stricter by design, on a comparator that is an identity
function.** The legacy pipeline runs **no** source readback at the per-slide render
checkpoint for a non-rescue slide — `source_reflow_rescue_readback` is gated on
`rescue is not None` (`authored_presentation_pipeline.py:1283-1289`) and the deck-level
readback happens only at publication
(`authored_admission_publication.py:36-52`). So the render comparator is
`lambda value: value` (`authored_admission_checkpoint.py:148`) and any gate rejection is
a mismatch by construction. Design §2.3 sanctions exactly this: "an **evidence
checkpoint** may hold any captured admission, including `rejected` verdicts (the
first-pass render before repair is evidence; the repair loop consumes it), marked
`publishable=false`".

**Would these 2 rows have changed run-24's outcome under enforce? No.**

1. `prepare_checkpoint_write` passes `evidence=(stage == "render")`
   (`authored_admission_checkpoint.py:152`). In enforce, `check_producer` **skips**
   `result.require()` for an evidence producer and instead stamps the receipt
   `persistence_class="evidence"` (`authored_admission_session.py:233-238`). No exception
   is raised, so the deck does not die earlier.
2. The checkpoint writer accepts it: `require_admission_receipt` permits a non-admitted
   verdict precisely when `persistence_class == "evidence"` and `html_repr == "captured"`
   (`authored_admission_receipt.py:133-137`), and both `metrics` and `png` are present so
   `CAPTURE_MISSING` (`:150-151`) does not fire.
3. No slide is flagged either. Flagging on this path comes from the terminal policy and
   from `source_visibility_flag_entries` at publication
   (`authored_admission_publication.py:292-297`); a rejected *render* receipt produces no
   flag entry of its own.
4. The only downstream effect is that `receipt["publishable"]` is false
   (`authored_admission_receipt.py:89-93`), so `checkpoint_stage_html(…, publishable=True)`
   returns `None` (`services/authored_checkpoint_html.py:44-67`) and
   `_persist_publishable_checkpoints` would rewrite that slide's render checkpoint at
   publication (`authored_admission_publication.py:175-193`) — one extra write, not a
   failure. A secondary cost: `AdmissionCaptureStore.remember` refuses to cache a rejected
   capture (`authored_admission_receipt.py:173-175`), so the capture-once rule is lost for
   that slide.

Run-24 never reached publication — it died at slide 20 in the author stage (progress
`current_stage: "author"`, `stage_counts.render: 33` of 35) — so items 3 and 4 are moot
for this run. **Under enforce, run-24 dies at the same place, for the same reason.**

### Run-24's actual deck-killer was a parity row, not a mismatch

Line 561 `[authored] slide 20 recovered on the simplified attempt` → line 562
`author_rescue rejected → rejected (source_content_browser_readback_failed)` → line 563
`slide_failure … retryable:false`. The rescue comparator
(`services/authored_admission_legacy.py:56-91`) and the gate agreed. The two `author`
mismatches immediately before it (559, 560) are the raw and expanded entries of that same
rescue attempt — the gate saw the same failure two steps earlier. Enforce changes nothing
about the outcome, only how much work is done before it.

### G4 — `publication`, `unobserved → error` / `authored_admission_internal_error` (1 row, line 1297, op `a69552b1`) — **blocker**

**Verdict: (2) gate bug, and the only observed row that changes a deck's outcome under
enforce.**

`final_readback` wraps the per-slide publication admissions in a bare
`except Exception`; in shadow it records this row and falls back to
`_legacy_final_readback`, but **in enforce it re-raises**
(`authored_admission_publication.py:205-226`, the `if session.mode == "enforce": raise` at
`:216-217`). Operation `a69552b1` is the deck that **published successfully** (line 1298,
`[authored] done … slides=35`). Under enforce that same deck would have failed with an
uncoded internal error.

The escaping exception cannot be identified from the log: it is swallowed without any
bounded record of its type. `admit_slide_html` deliberately re-raises a `ValueError` that
carries neither a `.code` nor the exact `SOURCE_READBACK_FAILED` string
(`authored_admission.py:223-230`), and `session.admit` itself can raise from
`metrics[index]` / `session.contexts[index]` indexing
(`authored_admission_publication.py:206-214`,
`authored_admission_session.py:51-59,61-80`). That this deck carried 8 terminal flagged
slides (line 1295) and `metrics_readback ledger=35 … gathered=0` (line 1296) makes the
flag/metrics shapes the first places to look. Reproducing it offline needs the stored
publication inputs, not this log.

## 4. Recommendation

**Fix first — do not switch `gate_mode` to `enforce` yet.** The author/render mismatches
are all explained and outcome-neutral, but one row in this very log is a proven regression.

Ordered list:

1. **`authored_admission_publication.py:205-226` — the publication internal error.**
   Reproduce and fix the exception escaping `session.admit` during `final_readback`. Until
   it is fixed, enforce converts at least one deck that publishes today into a deck-level
   failure. (Blocker.)
2. **Bounded exception evidence in the shadow row.** `final_readback`'s `except Exception`
   discards the exception class. Add a bounded `gateError` field (class name only, same
   `bounded_code` treatment) so a step-1 "unexplained difference" can actually be
   explained. Without this, item 1 cannot be diagnosed from a live series.
3. **Put `predicate` and a `blockCount` into the shadow line**
   (`authored_admission_shadow.py:36-42`). Both already exist on the receipt
   (`authored_admission_receipt.py:48,78-80`) and both are bounded and content-free.
   Today a mismatch group can only be attributed by reading the code and counting row
   adjacency, which is what this document had to do for G1 and G3 and what it could not do
   for G3's four candidate predicates.
4. **Decide and record the raw-entry (`token`) binding question.** `provider_raw_html`
   binds source text into model output before `_postprocess_slide` has finished shaping
   it. Zero counter-examples in 872 rows is encouraging but is not a proof; and under
   enforce that entry point also starts *returning* stamped HTML into the middle of
   `_postprocess_slide` (§3 G1, residual (b)), which shadow never exercises. Either (a)
   make the raw entry diagnostics/policy-only and keep binding at the expanded entry, or
   (b) add a replay over the stored run-13/14/17/18/19/23 fixtures named in design §3.2
   asserting raw-entry and expanded-entry verdicts agree. This is cheap and removes the
   only unproven strictness in G1.
5. **Then re-run one shadow series with items 2–3 landed** and confirm the table shows
   only the four explained groups. Design §4.1's exit criterion is "zero *unexplained*
   verdict differences"; after items 2–4, G1/G2/G3 are explained by construction (empty
   comparator at that entry point) and can be declared expected rather than investigated
   per run.

**Not blocking enforce:** G1, G2, G3. All three are the gate applying a predicate the
legacy path either applies later or does not apply at all, and none of them changes
run-24's outcome. Run-24 fails at slide 20 under shadow and under enforce alike, with
`source_content_browser_readback_failed` from the same binder, on a row where the gate and
the legacy rescue comparator already agree.
