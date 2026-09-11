# Independent implementation review — T3 normalization, T4 provider overload, T5 placeholder authoring

Date: 2026-09-11. Scope: commits `c1ecc37f3` (T3), `5590702f7` (T4), `c87aebd26` (T5) on
`serve/main-20260902`, reviewed at HEAD `5df4dd3cf` (working tree also contains `5208a895e`).
T2 (gate) and T6 (repair loop) belong to another reviewer; they are referenced only where a T3/T4/T5
seam lands in them.

**Scores: PPT quality 6/10 · Speed 6/10 · Architecture 5/10.**

Read-only review. No source, test, config or stack was modified. No provider call, no stack contact,
no database access, no secret read. The only writes are this file and throwaway probe scripts in the
session scratchpad. Every row marked **Verified** was produced by running the repository's own code in
`servers/fastapi/.venv` against stored fixtures under
`artifacts/verbatim-design-measurements-20260911/fixtures/`; rows marked **Inference** are expected
consequences that were not executed.

Citation convention: `B/` = `servers/fastapi/`. Line numbers are from the reviewed tree.

---

## A. Scores

### A1. PPT quality — 6/10

**What is genuinely right (all Verified by execution).**

The substitution contract holds against the adversarial cases the design review (P1-2) asked for.
Running `expand_placeholders` over hand-built fixtures:

| case | result |
|---|---|
| source text `<script>alert(1)</script> 위험` inserted through a whole-block token | serialized as `&lt;script&gt;alert(1)&lt;/script&gt; 위험` — no injection |
| source text `x" onmouseover="alert(1)` | inserted as text, escaped context is never an attribute |
| source text of block `b1` literally containing `{{q:b2}}` | inserted as data, **not** re-expanded (`B/services/source_content/placeholder/substitute.py:132-150` parses once, plans ownership before any insertion, and never rescans) |
| token in an attribute / `<style>` / comment / `hidden` subtree | `source_placeholder_context` each time (`B/.../tree_slots.py:38-57`) |
| duplicate whole-block token, unknown id | `source_placeholder_duplicate`, `source_placeholder_unknown` (`B/.../static_checks.py:17-31`) |
| ranges emitted out of DOM order (`@5-12` then `@0-5`) | rejected: the owner's rendered text fails `projection.accepts` (`B/.../substitute.py:102,118`) |
| range boundary splitting a ZWJ emoji cluster or a combining sequence | `source_placeholder_cover` — boundaries come from `regex.finditer(r"\X", text)` (`B/.../block_table.py:78`) |
| whole-block token in an SVG `<text>` or a `<td>` | expands and stamps correctly |

Escaping is the serializer's, not a hand-rolled escape: `_insert` only assigns `etree` `text`/`tail`
and `serialize_authored_html` escapes on output (`B/utils/authored_html_tree/serializer.py:15-32`).
`escape_rcdata=False` is correct there because `<style>`/`<script>` are already a forbidden token
context, so no source text can reach a raw-text element.

Tree normalization does not change rendering on real material. Over **199 stored authored slides**
(every `html-author.txt` in the fixture export):

- 0 slides changed the legacy rule set (legacy and tree fire an identical, in fact empty, receipt set);
- 0 slides were non-idempotent under a second tree pass;
- 0 slides were rejected by `reject_hidden_overflow_declarations`;
- 155 admitted under `admit_authored_html`, and all 44 rejections are the expected
  `data-illust-prompt` authoring-only attribute, which the real path strips
  (`B/utils/llm_calls/author_slide.py:713-717`).

The enumerated rewrite pass carries every legacy rule with the same order and the same predicates —
break-control CSS, hidden/bound-text, title `<br>`, `name`, disallowed CSS and at-rules
(`B/utils/authored_html_tree/rewrite_rules.py:191-198` versus
`B/utils/authored_html_normalize.py:144-194`), and the CSS predicates are now literally shared
(`strip_break_control_declarations`, `strip_disallowed_declaration_list`,
`strip_disallowed_stylesheet` in `B/utils/authored_css_strip.py`). The run-13 shape is handled:
`.c::after{content:'x'}` → `.c::after{}` with `css_property_stripped(content:text)` (Verified).
The run-18 shape is handled: `</img>` is dropped by html5lib with a
`html_void_end_tag_dropped(img)` receipt (`B/utils/authored_void_end_tag_normalize.py:68-86`).
Raw diagnostics preserve the duplicate-attribute and unbalanced-tag evidence the tree would erase
(`B/utils/authored_html_tree/raw_diagnostics.py:70-98`), and the strictness matches today's policy
exactly — implied end tags (`<ul><li>a<li>b</ul>`) are rejected by both, so tree mode is not more
permissive (Verified).

**Why this is not 8+.** The design's own claim is "every source text block is published exactly as
written", by construction. It is not. Token expansion inserts the projection's *canonical accepted
text*, which drops every run marker — and `projection.accepts` treats markers as optional, so nothing
downstream can notice.

The worst instance is in the very fixture the design named as the bound acceptance contract
(the 43-page / 96-image manifest, design §3.4). **Verified**:

```
raw block   source-slide:1:text:2 = '㈜아이비즈소프트웨어'   (㈜ = U+321C)
canonical                          = '아이비즈소프트웨어'
expanded HTML                      = <p data-qid=…>아이비즈소프트웨어</p>
projection.accepts(whole, canonical) = True
```

`_ENUMERATORS` at `B/services/source_content/source_runs.py:25` contains the range `㈀-㈜`, so the
corporation mark `㈜` is classified as a presentational enumerator and peeled off the run body.
Across the 43-page manifest that marker appears **45 times** (Verified; the other markers are
`①②③④`, 7 more runs — 52 marker runs of 1,317, 59 characters of 22,489). Today the model retypes the
raw string and `accepts` admits the full form, so the character survives; under placeholder
authoring the server *guarantees* its absence unless the model voluntarily re-renders it as
furniture. The prompt only says "render them as list/icon furniture **if desired**"
(`B/services/source_content/placeholder/block_table.py:114-116`).

Second instance, also Verified: a two-item bullet block

```
'• 첫째 항목\n• 둘째 항목'  →  <li>첫째 항목 둘째 항목</li>
```

admitted, `owners=1`, no deviation recorded. Both bullets and the hard line break are gone. A model
that follows `hardBreaks` and uses two range tokens gets the break back (Verified: two `<li>`s), but
nothing in the static checks requires it, and the bullets are gone either way.

Third: the canonical text's inter-run separator is a real character that must be covered by some
range, so a compliant line split leaves a trailing (or leading) space inside a list item. Cosmetic,
low severity.

Net: fidelity of *letters the model used to retype* goes up and the run-17/19/23 class of binding
failure disappears; fidelity of *glyphs the projection classifies as furniture* goes down, from
"usually present" to "guaranteed absent", with no gate able to see it.

### A2. Speed — 6/10

Parser cost is not a problem. **Verified** on 20 stored authored slides (mean 7,727 bytes):

| pass | ms/slide |
|---|---|
| legacy source-span normalization | 2.2 |
| raw diagnostics alone | 0.6 |
| full tree pass (diagnose + html5lib parse + rewrite + serialize) | 4.9 |

`admit_slide_html` runs `validate_authored_slide_html` twice (`B/services/authored_admission.py:173`
and `:186`) and `raw_diagnostics_step` once, and the tree entry point diagnoses again internally
(`B/utils/authored_html_tree/__init__.py:24`), so tree mode costs roughly 11 ms per admission against
roughly 5 ms for legacy. Against the measured 21.4 s/slide deck amortization this is noise.

Retry bounds are tight and cannot storm. **Verified by reading**
`B/utils/provider_overload.py:110-158`: at most `_MAX_OVERLOAD_PHYSICAL_CALLS = 2` physical calls per
logical call; the cooldown for the single replay is `OVERLOAD_COOLDOWN_BASE_SECONDS * 2**0` = 30 s,
capped at 300 s; the replay is skipped when `clock() + cooldown >= deadline`; each physical transport
timeout is shortened to the remaining budget (`B/utils/provider_idempotency.py:388-404`). With three
author content ordinals the worst case is 6 physical author calls and 90 s of added cooldown per
slide, all inside the per-call 360 s bound. The reservation is durable and taken *before* the replay
(`B/services/authored_provider_attempt_ledger.py:190-244`).

Two speed caveats.

- **The deadline used is not the operation deadline.** Every authored call site passes no
  `timeout_seconds` (`B/utils/llm_calls/author_batch.py:409-415`,
  `B/utils/llm_calls/author_slide.py:1102-1105`), so `deadline_seconds` falls back to the fixed
  `PROVIDER_CALL_TIMEOUT_SECONDS = 360.0` (`B/utils/provider_idempotency.py:461-465`). Design §2.4
  says "the operation deadline applies"; at this layer it does not. Not a regression — the previous
  bound was the same 360 s — but the design claim is not implemented.
- **Token accounting probably moves the wrong way.** The per-slide block table is
  1,387–6,855 characters, median 4,493, 177,132 characters for the 43-page deck (Verified,
  reproducing the T5 report). Measured author input for a 35-slide deck is 431k–512k tokens across
  41–49 calls. Adding ~4.5k characters of mixed Korean/ASCII JSON per call is on the order of
  +2k input tokens per call (Inference; no production tokenizer is installed), i.e. roughly +15–20%
  input, against the measured 1–3% output-token saving. 15,047 of the 22,489 canonical characters are
  emitted **twice** in the table because `prompt_row` always includes both `text` and `fullText` even
  when they are identical (`B/.../block_table.py:33`), which is ~8% of the table for no information.

Quota pause: bounded and durable, but see B6 — a multi-day pause is not demonstrably resumable for a
public-deadline operation.

### A3. Architecture — 5/10

**Right.** Overload classification is now one predicate, `is_authored_provider_overload`
(`B/services/authored_batch_policy.py:246-280`), with exactly three consumers: the bounded call
(`B/utils/provider_overload.py:135`), the outcome mapper (`B/utils/provider_idempotency.py:428`) and
the scheduling state (`B/services/authored_batch_policy.py:209`). Callers genuinely stopped
classifying: `_overload_recovery_allowed` and the caller-side replay were deleted from rescue
(`5590702f7` on `B/services/authored_author_stage_rescue.py:315-345`), and `register_rate_limit` now
reads `error.overload` / `error.cooldown_seconds` instead of inspecting HTTP text
(`B/utils/llm_calls/author_slide_admission.py:69-91`). The ledger matches
`.spec-memory/decisions/authored.provider-attempt-budget-per-operation-attempt.2.json`: ordinals
`{1,2,3}` (`B/services/authored_provider_attempt_ledger.py:112`), ordinal 3 reserved for rescue
(`RESCUE_ATTEMPT_ORDINAL = 3`, `B/services/authored_author_stage_rescue.py:76`), and the overload
replay recorded as physical ordinal 2 without advancing the content ordinal
(`:190-244`). The repair loop's `N` is a separate budget
(`AUTHORED_VERBATIM_REPAIR_LOOP_MODEL_CALLS`, default 1, clamped 0..3,
`B/utils/get_env_programme.py:279-283`) and does not touch author ordinals, so there is no
contradiction with the decision file.

The grammar and scanner are bounded and robust: id pattern capped at 192 characters, offsets capped
at 7 digits (`B/.../tokens.py:10-11`), nested/split/adjacent-brace forms rejected (`:30-43`), the
block table built only from trusted server bindings and never from model JSON
(`B/.../block_table.py:52-53`), receipts bounded at 64 entries / 120-character details
(`B/utils/authored_html_tree/receipts.py:7-8`), deviations capped at 32
(`B/.../static_checks.py:49`). Knob wiring is correct and registry-synced: `AUTHORED_HTML_NORMALIZATION`
(enum, default `legacy`), `AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING` (bool, default false), both with
accessors in `B/utils/get_env_programme.py` and entries in `constants/settings_registry.json`; both
reports record `npm run test:settings-registry` passing, and the focused suites pass here
(**338 passed**, Verified: the 12 T3/T4/T5 unit files).

**Wrong.** Five structural defects, detailed in section B: the tree switch does not actually switch
representation (B1), T4's `authored_request` widening breaks the streaming author path and leaks
authored guards into unrelated calls (B2), the admission context cannot tell the representations
apart (B4), the two rollout switches are not independent (B5), and the "restricted to 429/5xx"
claim is a free-text match (B3).

---

## B. Problems, ranked

### B1 — P1. Tree normalization is discarded on every slide that fires no semantic rule

**Verified.** `B/utils/llm_calls/author_slide.py:723-726`:

```python
normalized = normalize_authored_html(canonical)
if normalized.normalizations:
    canonical = normalized.html
```

The guard is correct for `legacy`, which returns byte-identical HTML when nothing fires. It is wrong
for `tree`, whose whole point is that the output is a canonical reserialization *whether or not* a
rule fired. Over the 199 stored authored slides, the tree pass produces **zero receipts on 199/199**
(Verified), so through the only production caller, tree mode would leave every one of them
byte-unchanged and the html5lib tree would never reach `admit_authored_html`.

Failure scenario: a model emits `<table><tr><td>…` with no `<tbody>`, or misnests two inline
elements. html5lib repairs the tree, the repair produces no receipt, the repair is thrown away, and
the raw bytes go to the renderer exactly as in legacy mode. The operator believes
`AUTHORED_HTML_NORMALIZATION=tree` gave them browser-equivalent tree construction; it did not. The
corollary is that T3's headline measurement ("all 105 digests changed") describes
`normalize_authored_html_tree` called directly, not the behaviour of the switch in the product.

Secondary consequence: the representation becomes *mixed within one deck* — slides that happen to
trip a CSS or `hidden` rule are canonicalized, their neighbours are not — so
`AdmissionReceipt.html_digest` is computed over two different representations in the same run.

Fix: in tree mode, always take `normalized.html`. One line, plus a test that a receipt-free document
still comes back canonical.

### B2 — P1. T4 widened `authored_request` and broke the streaming author path

**Verified by reproduction.** `B/utils/provider_idempotency.py:305-308` changed the authored guard
from `outcome_scope == "authored"` to

```python
authored_request = (outcome_scope == _AUTHORED_OUTCOME_SCOPE
                    or selected_retry_reserver is not None)
```

where `selected_retry_reserver` is non-`None` whenever `current_provider_attempt_reservations()` is
non-empty (`:244-272`). `take_provider_attempt_reservations()` has **no production caller** (Verified
by repo grep; only `authored_checkpoint_state.py` re-exports it and one test calls it), so once a
slide reserves an ordinal the ContextVar stays populated for the rest of that task.

The streaming author path deliberately omits `outcome_scope`, and says so:

> `B/utils/llm_calls/author_slide_stream.py:325-329` — "No `outcome_scope`: the authored outcome
> guard inspects the returned object's text, and a stream returns an iterator, not a document."

T4 re-enables that guard through the back door. `llmai.chatgpt.client.generate(stream=True)` returns a
generator (`.venv/.../llmai/chatgpt/client.py:979-990`, `_generate_responses_stream` is a generator
function), so `_content_has_text(getattr(response, "content", None))` is `False` and
`B/utils/provider_idempotency.py:437-443` raises before a single event is read.

Reproduced with a fake client inside a running loop:

```
with a pending ProviderAttemptReservation : stream call FAILED -> ProviderOutcomeError public_provider_empty_output
with _PENDING cleared (control)           : stream call OK -> generator
```

`AUTHORED_AUTHOR_STREAMING_ENABLED` defaults **false** (`B/utils/get_env_programme.py:230-234`), so
this is latent, not live. But it is a deck-killer the moment streaming is turned on, and streaming is
an independent throughput knob nobody would expect T4 to have disabled.

Same widening, second effect (**Inference**, not reproduced): any provider call made in a task where
authored reservations are pending is now treated as authored — including the generic structured
path `B/utils/llm_utils.py:174-179`, which passes no `outcome_scope`. Those calls now get
`max_retries=0`, the chatgpt outcome guard, `ProviderQuotaPause` raising, and a `reserve_retry`
closure bound to **the author slide's** reservation, so an overload replay of an unrelated call would
spend the author slide's physical ordinal 2 and mis-attribute provider spend.

Fix: keep `outcome_scope` as the sole authority for the authored guards, and pass the reserver
independently (it is already an explicit parameter, `retry_reserver=`). Consume or scope `_PENDING`
so it cannot outlive its call.

### B3 — P2. "Classification restricted to 429/500/502/503/504" is a free-text match, and it now gates a physical replay

**Verified.** `B/services/authored_batch_policy.py:41`:

```python
_HTTP_OVERLOAD_PATTERN = re.compile(r"(?<!\d)(?:429|500|502|503|504)(?!\d)")
```

applied to `str(error)` and to `code`/`status_code`/`response.status_code` as strings
(`:260-279`), together with `_RATE_LIMIT_PATTERN` (`rate limit|usage limit|too many requests|
resource exhausted`) and `_TIMEOUT_PATTERN`. The T4 report's line 14 ("now restricted to
429/500/502/503/504, timeout, and provider-layer coded overloads") describes the intent, not the
predicate.

Before T4 a misclassification only slowed scheduling. Now it also authorizes a physical replay
(`B/utils/provider_overload.py:134-152`). Failure scenario: a provider returns
`400 invalid_request_error: max_output_tokens must be <= 500` — `(?<!\d)500(?!\d)` matches, the call
is classified overload, a 30 s cooldown is burned and the identical request is replayed once, which
fails identically. The blast radius is limited because `idempotency_capable` additionally requires
the OpenAI-key transport (see B7), but the predicate is the thing the decision file's
"Treat every generic provider bridge exception as transient" forbidden-item is aimed at.

Residual second classifier (**Verified**, lower severity):
`B/services/authored_checkpoint_errors.py:265-279` still interprets `429`/`>=500` itself to decide
operation retryability. Different axis from overload scheduling, so arguably out of the design's
sentence, but it is a second place where an HTTP status is read.

### B4 — P2. The admission receipt cannot distinguish representations; `html_repr` does not exist

**Verified.** `AdmissionContext.policy_revision` is the constant `"authored-admission/v1"`
(`B/services/authored_admission_policy.py:8`), and `identity_digest` is a digest of the context
fields (`B/services/authored_admission_context.py:70-72`). Neither the normalization mode nor the
placeholder knob participates. Repo-wide grep for `html_repr` returns **nothing**, although design §4
step 3 requires token mode to be "pinned per operation and recorded in the checkpoint (`html_repr`)"
and §2.3 makes `html_repr=token` a checkpoint state.

`get_authored_html_normalization()` is read live on every call
(`B/utils/authored_html_normalize.py:430`), so flipping the knob mid-flight changes the
representation of an in-progress deck while the receipts claim the same policy revision.

Failure scenario: a deck authors 20 slides with `legacy`, an operator flips to `tree`, the job
resumes; admitted receipts from before the flip still match by digest-and-context even though the
two halves are different representations, and no receipt records which one produced which bytes.

Related dead parameter (**Verified**, cosmetic): `expand_placeholders` reads
`getattr(context, "normalization_profile", "document")` (`B/.../substitute.py:134`), but
`AdmissionContext` has no such field, so the profile is always `"document"`.

### B5 — P2. The normalization switch and the placeholder switch are not independent

**Verified.** `expand_placeholders` parses with `parse_authored_html` and returns
`serialize_authored_html(parsed)` unconditionally (`B/.../substitute.py:135,150`). So turning
`AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING` on adopts the canonical HTML5 serializer for every
source-reflow slide **even when `AUTHORED_HTML_NORMALIZATION=legacy`**, invalidating stored capture
identities for those slides. Design §4 promises four independent switches rolled out in order, with
the parser step measured before the token step; as built, step 3 silently includes step 2 for the
slides it touches (and, because of B1, delivers it *more* reliably than step 2 does).

### B6 — P2. A multi-day quota pause is durable but not demonstrably resumable

**Verified.** `ProviderQuotaPause.retry_after_seconds = max(0.05, reset_at - now)`
(`B/utils/provider_overload.py:58`); the runner schedules
`next_attempt_at = now + deferred.retry_after_seconds` with no cap
(`B/services/workspace_operation_runner.py:3243-3244`). For a public v2 admission the same function
first checks `_public_deadline_unavailable(...)` and converts the claim to a hard
`public_deadline_exceeded` failure (`:3277-3285`).

Failure scenario: the account exhausts on 2026-09-11 with `resets_at` on 2026-09-15. A public deck is
deferred ~345,600 s out. At the next poll the public deadline has long expired, so the operation
fails `public_deadline_exceeded` rather than resuming. The persisted detail is honest about this —
`provider_quota_resume_semantics = "coordinator_requeues_after_reset"`
(`B/utils/provider_overload.py:59-62`) — but the design's "durable pause with a resume path" is, for
public decks, a manual coordinator action, not an automatic one. The two receipt keys are correctly
allowlisted in the central sanitizer (`B/services/workspace_operation_runner.py:260-261`).

Minor, same area (**Inference**): raising `ProviderQuotaPause` out of the middle of a wave
(`B/services/authored_wave_scheduler.py:152-153`) skips `record(...)` for the remaining batches of
that wave, so slides that already authored successfully in the same wave are not checkpointed and
are re-authored on resume.

### B7 — P3. The overload replay is inert on the production transport

**Verified.** `idempotency_capable` requires `supports_provider_idempotency`, i.e.
`provider_id in {"openai"}` **and** the client module starting with `llmai.openai.`
(`B/utils/provider_idempotency.py:30-31,236-241,451-455`). The authored path's chatgpt/codex
transport (`llmai.chatgpt.`) fails both tests, so `BoundedProviderCall` re-raises immediately without
retrying. Run-22's failure mode (429 on rescue, on that transport) therefore still ends the same way;
what changed is that the caller-side replay in rescue was removed (`5590702f7`), i.e. that path now
has strictly *fewer* retries than before.

This is a deliberate, defensible choice — the decision file forbids "Replay inside one admission when
the provider transport has no confirmed idempotency support" — but it means the live series after the
quota reset cannot validate the replay on the default configuration unless a provider is selected for
which it is enabled. The T4 report's "only remaining product-path evidence" should say so.

### B8 — P3. An expansion-induced raw-diagnostics violation escapes the gate uncoded

**Inference** (narrow). `normalize_authored_html_tree` raises `RawAuthoredHtmlViolation(ValueError)`
(`B/utils/authored_html_tree/__init__.py:26`), which carries `.diagnostics` but no `.code`.
`admit_slide_html`'s `except ValueError` branch requires `hasattr(error, "code")` or an exact
`SOURCE_READBACK_FAILED` message and otherwise **re-raises**
(`B/services/authored_admission.py:226-233`). The first `validate_authored_slide_html` cannot trip it
(the gate ran `raw_diagnostics_step` already), but the *post-expansion* call at `:186` runs on
expanded bytes, where inserted source text could cross `MAX_TEXT_BYTES`. The escape violates design
§2.3's "No layer collapses a code into `authored_unexpected_failure`". Give
`RawAuthoredHtmlViolation` a `.code` (the first violation's code) and the branch handles it.

### B9 — P3. `placeholder_prompt_for_slide` can raise, and its 64,000 limit is new, not inherited

**Verified.** `placeholder_prompt_contract` raises a bare `ValueError` above 64,000 characters
(`B/.../block_table.py:99-100`) and `build_block_table` can `reject(...)` with
`source_placeholder_context` when a projection has no canonical accepted rendering (`:76-77`). Both
propagate out of `author_prompt` (`B/utils/llm_calls/author_slide_prompt.py:460-462`), i.e. out of
*prompt construction*, where the author stage's error envelope does not expect a source-readback
error.

Separately: the T5 report calls 64,000 "the existing structured-context limit". Repo-wide grep finds
no such constant anywhere outside `block_table.py:99` and its own test
(`B/tests/unit/test_source_placeholder_prompt.py:52,56`) — it is a **new self-imposed bound**. It also
bounds only the table, not the assembled prompt, and the table is appended to an already-large
`structured_contract`. The per-unit sizes (max 6,855) make this comfortable for the 43-page fixture,
but the safety argument should not be stated as inherited.

### B10 — P3. Contradictory instructions remain in the prompt

**Verified.** The placeholder contract is appended to `structured_contract`
(`B/utils/llm_calls/author_slide_prompt.py:460-462`) while `B/utils/llm_calls/author_slide_rules.py:215`
still instructs "copied VERBATIM from THIS slide's CONTENT. Never invent a word, number, percentage",
and the slide CONTENT still carries the source text. The contract's
"Emit `{{q:<id>}}` … never retype its text" has to win by position. Nothing removes or conditions the
older rule when the knob is on. **Inference**: the most likely first-series failure is
`source_placeholder_missing` from a model that follows the older, more emphatic instruction.

Also **Verified**: `placeholder_prompt_for_slide` returns `""` when `current_session()` is `None`
(`B/services/source_content/placeholder/prompt.py:15-17`), while the gate decides token mode from the
knob plus the context alone (`B/.../text_step.py:18-22`). A producer that authors outside an
admission session therefore gets no contract but is still held to it — every slide would fail
`source_placeholder_missing`. Batch authoring is fine (`B/utils/llm_calls/author_batch.py:139` goes
through the same `_author_prompt`), and rescue is fine for the same reason.

### B11 — P1 for product acceptance. Markers classified as furniture include content

Covered in A1 with the `㈜` evidence. Stated here as a ranked problem because it is the one defect
that makes published bytes wrong while every gate says admitted.

`_ENUMERATORS` (`B/services/source_content/source_runs.py:25`) is a projection-level classification
that predates this work; placeholder authoring converts it from "tolerated, usually invisible" into
"guaranteed loss". Narrowest correct fix, entirely inside T5: keep dropping markers drawn from
`_ITEM_GLYPHS` (bullets, checkboxes, arrows — genuine template furniture) and **keep enumerators,
`n.`/`n)` numbering and `㈜`-class marks in the canonical text** built at
`B/.../block_table.py:63-79`. `accepts` admits the full form as well as the stripped one
(`accepted_texts`, `B/services/source_content/source_runs.py:114-127`), so this change cannot make a previously-passing block
fail. Do not edit `_ENUMERATORS` itself: it is shared with the binder and the existing run binding
ids.

---

## C. Improvements, with cost

| # | Change | Why | Cost |
|---|---|---|---|
| C1 | Keep non-furniture markers (enumerators, `n.`, `㈜`) in the canonical token text; drop only `_ITEM_GLYPHS` markers | Resolves B11/A1; `accepts` already admits both forms so nothing regresses | Low — one predicate in `block_table.py` plus a fixture asserting `㈜아이비즈소프트웨어` round-trips |
| C2 | In tree mode always adopt the reserialized HTML, receipts or not | Resolves B1; makes the switch mean what it says | Low — one condition, one test; re-run the digest parity table afterwards because it will now apply to 100% of slides |
| C3 | Restore `outcome_scope` as the only authored-guard authority; pass `retry_reserver` explicitly; clear `_PENDING` when a logical call completes | Resolves B2 before anyone enables streaming | Medium — touches three call sites plus the reserver lifetime |
| C4 | Classify overload on structured status only (`status_code`, typed SDK exceptions, provider-layer codes); keep the text patterns for *scheduling* but not for authorizing a replay | Resolves B3 and the decision file's forbidden item | Medium — `is_authored_provider_overload` grows a strict variant; existing scheduling behaviour unchanged |
| C5 | Put the normalization mode and the placeholder mode into `AdmissionContext` (as `policy_revision` components or a real `html_repr`), and pin them per operation | Resolves B4 and delivers rollout step 3's stated contract | Medium — context field, receipt schema, resume path |
| C6 | Give `RawAuthoredHtmlViolation` a `.code`; catch `ValueError` from `placeholder_prompt_contract` / `build_block_table` at the prompt boundary and map to a coded author failure | Resolves B8 and B9's first half | Low |
| C7 | Emit `fullText` only when it differs from `text`; drop `pipes`/`markers` keys when empty | ~8% of the block table is an exact duplicate of `text` (15,047 of 22,489 characters, Verified) | Low |
| C8 | Reconcile `author_slide_rules.py:215` with the token contract when the knob is on | Resolves B10 | Low |
| C9 | Cap the quota-pause requeue delay and decide explicitly what a public-deadline deck does across a multi-day reset | Resolves B6 rather than discovering it on 2026-09-15 | Medium — runner policy, not provider code |

### What must be verified live after the 2026-09-15 quota reset

1. **A real 429 receipt.** Confirm which transport the authored path actually uses, and therefore
   whether `idempotency_capable` is true (B7). If it is false on the default model, the replay was
   never exercised and the T4 acceptance claim must say "not applicable on the default transport".
2. **A real `usage_limit_reached` → queued → running transition**, including whether
   `next_attempt_at` days out survives the poller and the workspace cleanup sweeper, and what a
   public-deadline deck does (B6).
3. **Tree mode on fresh output, after C2.** Every slide will change digest; confirm Chromium capture,
   the Hybrid text-run assembler and the PPTX export are unaffected, and that
   `reject_hidden_overflow_declarations` still passes on freshly authored (not stored) bytes.
4. **Token mode on the 43-page fixture, not the 35-slide one.** Specifically: does the published deck
   contain `㈜` (45 expected occurrences), do bullet/enumerated blocks keep their list furniture, and
   what fraction of first attempts fail `source_placeholder_missing` because of B10.
5. **Measured token delta** with real usage receipts: author input tokens per call with and without
   the block table, against the 431k–512k baseline. The design's 1–3% output saving is the small
   number; the input growth is the one that has not been measured.
6. **Streaming × placeholder × tree together**, once C3 lands — the three knobs interact and no
   combination has been run.

---

## D. Verdict

**Fix first, then ship behind the switches — do not ship any of the three as defaulted.**

The three switches must stay at `legacy` / `false` / `false` until at least the following are done,
because each is a defect in the *implementation*, not a rollout risk:

1. **B11 / C1** — markers that carry content are dropped from published text and no gate can see it.
   This is the one problem that makes token mode worse than today on the named acceptance fixture.
2. **B1 / C2** — tree mode does not apply its own serializer on 199/199 real slides, so the switch
   does not deliver what it is being rolled out to deliver, and the representation becomes mixed
   within a deck.
3. **B2 / C3** — T4 silently disabled the streaming author path; this must be fixed before anyone
   turns `AUTHORED_AUTHOR_STREAMING_ENABLED` on, and before the widened `authored_request` reaches
   the generic provider path.

B3 (C4), B4 (C5), B6 (C9) and B8-B10 (C6-C8) should be fixed before the live series, but none of them
alone blocks a gated experiment.

What is genuinely good, and should not be relitigated: the substitution security boundary holds under
every adversarial case the design review asked for; the tree rewrite pass preserves every legacy rule
on real material and is idempotent; overload classification really is centralized and callers really
did stop classifying; the physical-call bound, the reservation and the cooldown maths are tight and
cannot storm; and the ledger matches its decision file. The distance between 5-6/10 and 8/10 here is
five small, well-localized fixes, not a redesign.

---

### Verification performed for this review

Executed in `servers/fastapi/.venv` (read-only, offline): the T3/T4/T5 unit suites
(`test_authored_html_tree`, `test_authored_html_raw_diagnostics`, `test_authored_html_normalize`,
`test_authored_css_strip`, `test_source_placeholder{,_prompt,_fixtures}`,
`test_authored_provider_outcomes`, `test_authored_batch_policy`,
`test_authored_provider_rate_limit`, `test_authored_wave_scheduler`,
`test_authored_author_stage_rescue`) — **338 passed**; legacy-versus-tree normalization, idempotence,
hidden-overflow and renderer-policy comparison over **199** stored `html-author.txt` documents;
per-pass timing over 20 of them; marker and canonical-text statistics over the **965** text blocks and
**1,317** runs of the 43-page contract manifest; block-table encoding sizes for all 43 units; twenty
hand-built substitution fixtures (injection, double expansion, forbidden contexts, ranges, graphemes,
ordering, SVG/table owners); and a fake-client reproduction of the streaming regression. No provider
call, no stack contact, no database access, no secret read, no source or test modified.
