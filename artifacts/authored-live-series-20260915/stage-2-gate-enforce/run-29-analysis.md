# run-29 (stage 2, `AUTHORED_ADMISSION_GATE_MODE=enforce`) — author-stage rescue is unreachable

Analysis date: 2026-09-12. Read-only; no code, stack or running series was touched.

| | |
|---|---|
| Run | `artifacts/authored-full-product-validation-20260910/run-29/` |
| Operation | `368884bd-5a6b-471c-b8a8-aa724efb1d26` |
| Presentation | `d4cfd860-f1a8-4a34-852e-d7a642576c3e` |
| Code | `40599846f` (verified: `git diff 40599846f HEAD` is empty for every file cited below) |
| Worker log (enforce) | `app_data/logs/worker-generation-20260912-052740.stderr.log` |
| Worker log (stage 1 shadow, run-24) | `app_data/logs/worker-generation-20260912-025947.stderr.log` lines 4–564 |
| Deck shape | `source_mode: source_content_restyle`, `content_policy.text: verbatim` → **source_reflow**, 35 slides, `luna-medium`, style `hyundai-culture-center` (`04-generate-request.json`) |

**Verdict: stop the stage and fix.** This is an enforce-specific regression, it has a single-point fix, and
enforce as shipped produces zero admission evidence — so continuing the stage cannot produce the comparison
the stage exists to produce.

---

## 1. What actually happened

Attempt 1 lost **five** slides in one burst, every one of them identical in shape
(log lines 170–174):

```
slide_failure ... "code":"authored_slide_author_failed","provider_attempt_count":2,
                  "retry_reason":"retryable_failure","retryable":true,"stage":"author"
   slide_number: 5, 12, 13, 24, 33
```

The burst shape is `services/authored_preauthor_scan.py:129-167` (`report_unresolved_slides`): it fails the
author stage for **every** unresolved slide and then raises the first one — which is why
`06-final-status.json` carries slide 5 in `error` and slide 33 in `failure`, both with `attempt_number: 1`.

The envelope (`code: authored_slide_author_failed`, `cause: source_content_browser_readback_failed`,
`retryable: true`, `provider_attempt_count: 2`) is minted by
`services/authored_author_stage_deferral.py:122-154` (`author_stage_failure`). That is the only producer of
that exact combination, so the failure travelled the **batch / pre-authoring lane**, not the per-slide tail.

The operation then retried (line 175, `retry_scheduled`, `error_code: "other"`). Attempt 2 ran 712 s, got
*past* authoring (no `slide_failure` lines at all), through VQA, declared 10 terminal slides
(lines 217–226, `gate repair not admitted, declaring terminal slide=10,12,13,15,16,17,19,25,29,31`),
flagged them (line 295, 0-based `[9, 11, 12, 14, 15, 16, 18, 24, 28, 30]`) — and failed anyway, because a
source_reflow deck may not publish flagged slides. The status envelope still reports attempt 1's author
failure, so **attempt 2's real cause is not in the receipt at all**.

Not one rescue line was emitted in the whole enforce log:

```
grep "budget spent|simplified attempt|deferred to the tail's rescue"  →  0 matches
```

Against the shadow log for the identical binder class:

```
025947.stderr.log:501  slide 13 unresolved after its author budget
                       (BrowserSourceReadbackError:source_content_browser_readback_failed);
                       deferred to the tail's rescue instead of failing the deck
025947.stderr.log:513  slide 13 author budget spent (authored_provider_attempt_budget_exhausted);
                       one simplified attempt
025947.stderr.log:552  slide 13 recovered on the simplified attempt
```

Shadow deferred and **recovered** slides 13, 32, 7, 12 and 27 on the third attempt from exactly this error
class. Enforce recovered none.

---

## 2. Q1 — why the slide ended after two provider attempts with no third (rescue) attempt

Neither of the two hypotheses in the question is the cause. The gate does not raise a "non-rescuable error
class", and `author_failure_is_rescuable` is not failing to recognise the exception class. **The rescue path
is not bypassed in enforce either — it is entered and it declines, because enforce moves the rejection
upstream of the one seam that attaches the rejected candidate.**

### The chain, with line numbers

**(a) The gate raises a bare `BrowserSourceReadbackError` that carries no rescue vocabulary.**

`services/authored_admission.py:84-92`

```python
def require(self) -> Admission:
    if self.verdict == "rejected":
        raise BrowserSourceReadbackError(
            self.code, detail=self.predicate,
            block_ids=self.block_ids,
            slide_indices=(self.receipt.context.slide_index,),
        )
    return self
```

`Admission` already knows two things this exception drops on the floor:
`self.retryable` (field declared at `authored_admission.py:73`, computed at `:214` from
`rendered_verdict`, `:126-150`) and `self.html` — the rejected candidate. `require()` forwards neither.
`BrowserSourceReadbackError` (`services/source_content/source_stamp_dom.py:17-34`) is a plain `ValueError`
with **no `retryable` and no `rejected_html` attribute**.

**(b) In enforce this raise happens inside the batch author call; in shadow it cannot happen there at all.**

`services/authored_admission_session.py:234-239`

```python
if session.mode == "enforce":
    if not evidence:
        result.require()          # <- raises here, and only here
    ...
    return result.html, result
# shadow falls through to the legacy call below and returns ITS result
```

The enforce raise fires at the in-batch producer entries
`utils/llm_calls/author_batch.py:242` (`provider_raw_html`) and `:288` (`provider_expanded_html`).
In shadow those two entries **cannot** produce a source-readback rejection, because their legacy callbacks
do no source work: `provider_raw_html` → `author_html(validate_policy=False, raw_input=True, bindings=None)`
whose `legacy` (`services/authored_admission_producers.py:37-42`) is the identity function;
`provider_expanded_html` → `legacy` is HTML validation only, no stamping. In shadow the readback rejection
can therefore only surface later, at `persist_batch_success`
(`services/authored_presentation_author_batch.py:21-25`, legacy `stamp_source_blocks`).

**(c) That relocation is what kills the rescue, because only the later seam attaches the candidate.**

`services/authored_presentation_author_batch.py:150-155` — the shadow seam:

```python
except BrowserSourceReadbackError as error:
    # The candidate is invalid, not a failed checkpoint write:
    persisted_failures[index] = runtime.AuthoredBatchFailure(
        error, error.detail, html        # <-- rejected_html attached
    )
```

`utils/llm_calls/author_deck.py:1554-1561` — the enforce seam:

```python
def _batch_failure(error: BaseException) -> AuthoredBatchFailure:
    rejected = getattr(error, "rejected_html", None)   # <-- gate error has none -> None
    return AuthoredBatchFailure(error, hint, rejected if isinstance(rejected, str) else None)
```

Note the surrounding code in `author_batch.py` sets `error.rejected_html = authored_html` by hand on every
`ValueError` it raises itself (`:276`, `:295`). The gate is the one rejection source that does not.

**(d) `author_failure_is_rescuable` then declines — correctly, on the evidence it was given.**

`services/authored_author_stage_deferral.py:210-241`

```python
if getattr(error, "retryable", None) is True:        # :235  None -> falls through
    return False
if isinstance(error, AuthoredProviderBudgetExhausted):# :237  no
    return True
if isinstance(getattr(failure, "rejected_html", None), str) and failure.rejected_html:
    return True                                       # :239-240  <-- SHADOW returns True HERE
return not author_failure_retryable(error)            # :241  not True -> False
```

Under enforce `rejected_html` is `None`, so line 239 misses and line 241 answers `False`.
No deferral → `defer_rescuable_author_failures` (`:244-292`) logs nothing and returns `{}` →
`report_unresolved_slides` fails and raises.

**(e) The tail-lane rescue would decline too.** For completeness: had this reached `process_slide`,
`services/authored_author_stage_rescue.py:306-307` is an unconditional

```python
if not isinstance(error, AuthoredProviderBudgetExhausted):
    return None
```

and `utils/llm_calls/author_deck.py:1121-1145` only produces that wrapper when
`_classify_author_validation_error` recognises the error. That classifier
(`utils/llm_calls/author_deck.py:312-508`) has **no branch for `source_content_browser_readback_failed` or
`BrowserSourceReadbackError`** — it returns `None`, and line ~1144 re-raises the raw gate error. So the
per-slide lane has the same hole, reached by a different route. Both lanes key on the same missing
attributes, which is why the fix belongs at `require()` and not in either lane.

---

## 3. Q2 — why the operation retried instead of deferring to the rescue

`retryable: true` is not a decision anyone made about this error. It is the catch-all default:

1. `services/authored_preauthor_scan.py:139-149` wraps the failure with `stage_failure(...)` →
2. `services/authored_author_stage_deferral.py:149` → `retryable=author_failure_retryable(error)` →
3. `services/authored_author_stage_deferral.py:106-119`:
   - not `AuthoredProviderBudgetExhausted`
   - not an output-token-limit chain
   - `terminal_provider_code(error)` is `None` (no `public_provider_*` code)
   - `getattr(error, "retryable", None)` is **`None`**, not a bool → falls through
   - → `is_retryable_exception(error)`
4. `services/authored_checkpoint_errors.py:265-279`: not a `RendererPolicyError`, no `status_code`, no
   `response.status_code` → **`return True`** (line 279, the unconditional tail).

So a deterministic, content-caused rejection — the model dropped source text block
`source-slide:33:text:6` — was classified as a transient transport failure and cost a full 712-second
operation retry. The gate itself already classified it correctly: `rendered_verdict`
(`services/authored_admission.py:131-144`) returns `retryable=False` for the `rendered_integrity` predicate,
and only `telemetry` (`:145-146`) gets `retryable=True`. That verdict is discarded at `require()`.

---

## 4. Q3 — enforce-specific regression? and the single-point fix

### It is enforce-specific, and the design statement is falsified twice

The design claim — *enforce changes only where checks run, not the attempt budget* — does not hold:

**(i) Enforce changes *what* is rejected, not only where.** The stage-1 shadow parity table for run-24 is
the proof, because it counts the calls where the gate rejected what the legacy path admitted:

| producer | existing verdict | gate verdict | gate code | count |
|---|---|---|---|---:|
| author_batch | **admitted** | rejected | `source_content_browser_readback_failed` | **76** |
| author_batch | admitted | rejected | `authored_slide_author_failed` | 1 |
| render | **admitted** | rejected | `source_content_browser_readback_failed` | **21** |
| author | **admitted** | rejected | `source_content_browser_readback_failed` | 2 |
| author_rescue | **admitted** | rejected | `source_content_browser_readback_failed` | 1 |
| author_batch | rejected | rejected | `source_content_browser_readback_failed` | 37 |
| structural_patch | rejected | rejected | `source_content_browser_readback_failed` | 21 |
| vqa_repair | rejected | rejected | `source_content_browser_readback_failed` | 3 |
| publication | unobserved | error | `authored_admission_internal_error` | 4 |

Roughly **100 admitted-then-rejected divergences per deck**. Under enforce every one of those becomes a hard
rejection that shadow never raised. Run-29 lost 5 slides in the author stage; shadow run-24 lost 1.

**(ii) Enforce shortens the effective author budget from 3 to 2 for this class**, as a side effect of moving
the rejection point (§2c above). That *is* an attempt-budget change.

### It did not, on this evidence, lose a deck shadow would have saved

Shadow run-24 also died: line 563,
`"code":"source_content_browser_readback_failed","provider_attempt_count":3,"retry_reason":"terminal_failure","retryable":false,"slide_number":20`.
The rescue's third attempt ran and also failed the readback, and because the deck is source_reflow the rescue
re-raised (`services/authored_author_stage_rescue.py:344-346`, `except Exception: if source_reflow: raise`).
So the honest statement is: **enforce did not make a passing deck fail — it made a failing deck fail worse
and less legibly**: no third attempt, a misclassified `retryable`, a wasted 712 s retry, and a receipt that
names attempt 1's cause for an attempt-2 failure.

### Enforce also destroys the stage's own evidence

```
grep -c "authored_admission_shadow"  052740.stderr.log (enforce) →     0
grep -c "authored_admission_shadow"  025947.stderr.log (shadow)  →  1989
```

`services/authored_admission_session.py:234-239` returns **before** `session.shadow.record(...)` (`:248`,
`:258`). Under enforce `AdmissionSession.shadow` stays empty for the whole deck, so
`admission_shadow_receipt()` and `scripts/authored_admission_shadow_report.py` have nothing to read.
The gate mode that is supposed to be measured is the one mode that emits no measurements.

### The generic fix, at one place

**`Admission.require()` — `servers/fastapi/services/authored_admission.py:84-92` — is the single boundary
where a gate verdict becomes an exception.** It should forward the two attributes the entire downstream
vocabulary already keys on, both of which the `Admission` object is already holding:

- **`rejected_html = self.html`** → picked up unchanged by `_batch_failure`
  (`utils/llm_calls/author_deck.py:1556`) and by `author_failure_is_rescuable`
  (`services/authored_author_stage_deferral.py:239`), restoring deferral → rescue → the third attempt,
  on both lanes, with no lane-specific code.
- **`retryable = self.retryable`** → short-circuits `author_failure_retryable`
  (`services/authored_author_stage_deferral.py:116-118`) before it reaches the
  `is_retryable_exception` catch-all `return True`
  (`services/authored_checkpoint_errors.py:279`), so a deterministic content rejection is terminal instead of
  buying a full operation retry. `rendered_verdict` (`authored_admission.py:126-150`) already assigns this
  correctly per predicate (`rendered_integrity`/`visibility` → False, `telemetry` → True).

That is one edit in one function. Everything else — producers, batch lane, tail lane, rescue, deferral,
retry policy — is left byte for byte as it is, and the `resumed_author_budget_is_rescuable` /
checkpoint-resume paths (`authored_author_stage_deferral.py:295-356`) keep working because they read the same
two fields off the persisted envelope.

**Second, smaller change, same file family:** emit the shadow row under enforce too
(`services/authored_admission_session.py:234-239` should record before it returns, with
`existing_verdict="unobserved"`). Without it stage 2 cannot be evaluated at all.

Do **not** fix this by adding a `source_content_browser_readback_failed` branch to
`_classify_author_validation_error` (`utils/llm_calls/author_deck.py:312`) or by teaching
`rescue_author_stage` a second accepted class — both are per-path patches that would fix one lane and leave
the other, and neither recovers the `retryable` misclassification.

---

## 5. Q4 — admission rows for operation `368884bd`

```
$ python scripts/authored_admission_shadow_report.py \
      app_data/logs/worker-generation-20260912-052740.stderr.log --operation 368884bd

| producer | existing verdict | existing code | gate verdict | gate code | predicate | blocks | gate error | count |
| --- | --- | --- | --- | --- | --- | ---: | --- | ---: |
```

**Zero rows — by construction, not by accident.** The report parses `authored_admission_shadow ` lines
(`scripts/authored_admission_shadow_report.py:11`); enforce emits none (§4 above). Running the same script
over the whole enforce log, with no `--operation` filter, also returns an empty table.

There is therefore **no rejected code, predicate or block list recorded anywhere for slides 24 or 33 in this
run**. `grep "slide 24|slide 33|source-slide:24|source-slide:33"` over the enforce log returns nothing beyond
the two `slide_failure` lines at 173–174. The only surviving evidence for slide 33 is the block id in
`06-final-status.json`:

```json
"block_ids": ["source-slide:33:text:6"], "cause": "source_content_browser_readback_failed"
```

A single missing/uncovered source text block puts this in the `rendered_integrity` branch of
`rendered_verdict` (`services/authored_admission.py:131-144`) — the branch whose correct classification is
`retryable=False`, and precisely the one that was reported as `retryable=true`.

For contrast, the stage-1 shadow log for the same binder class yields the 1989-row table reproduced in §4.
That asymmetry is the operational argument for stopping: stage 2 currently costs more per run than stage 1
and returns strictly less information.

---

## 6. Recommendation

**Stop stage 2 and fix `Admission.require()` before any further enforce runs.**

Reasons, in order of weight:

1. **The author-stage rescue is structurally unreachable in enforce for the gate's own dominant rejection
   code.** `source_content_browser_readback_failed` is the code behind essentially every gate rejection in
   the shadow table. Stage 2 is currently measuring a missing wire, not the gate's policy. Every enforce run
   until this is fixed produces a result that will have to be discarded.
2. **Enforce emits zero admission evidence.** The stage's deliverable is the shadow/enforce comparison; the
   enforce half is empty. Continuing burns ~12 minutes per attempt for receipts that cannot be diagnosed.
3. **Each failure now costs a wasted full-deck operation retry** (712 s in run-29) because the gate's own
   `retryable=False` verdict is dropped at `require()`.
4. The fix is one function, it is additive, and it restores behaviour the shadow lane demonstrably has
   (5 slides recovered on the third attempt in run-24). The risk of *not* fixing it first is that stage 2's
   headline number is dominated by an artefact.

Suggested sequence: land the two-attribute forward in `require()` plus the enforce shadow-row emission,
re-run a single enforce deck against the same fixture, confirm (a) non-empty
`authored_admission_shadow_report.py` output, (b) at least one `deferred to the tail's rescue` /
`recovered on the simplified attempt` pair, and (c) `retryable:false` on any surviving
`rendered_integrity` failure — then resume the series.

One item outside this fix, worth a separate ticket: **attempt 2 failed at publication (10 flagged terminal
slides on a source_reflow deck) but the reported envelope is attempt 1's author failure**
(`06-final-status.json`, both blocks `attempt_number: 1`). A retry's receipt should name the retry's cause.
