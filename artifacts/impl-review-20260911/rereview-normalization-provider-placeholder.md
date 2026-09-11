# Re-review — T3 normalization, T4 provider overload, T5 placeholder authoring

Date: 2026-09-11. Branch `serve/main-20260902`, reviewed at HEAD `4c0eb5a15`.
Subject: the two commits that claim to close the three blockers of
`artifacts/impl-review-20260911/review-normalization-provider-placeholder.md`:

- `3eba724d8` — `block_table.py` keeps non-item-glyph markers in canonical text; `author_slide.py`
  adopts normalization by byte comparison.
- `be712c25e` — `outcome_scope` restored as the only authored-guard authority; the attempt
  reservation is taken, not snapshotted; overload classification reads structured status first;
  the call receipt states `idempotencyCapable` / `replayUnavailable`.

`4c0eb5a15` (T2/T6 fixes) is in the tree but is another reviewer's subject; it is referenced only
where it moves a seam named below.

**Scores: PPT quality 7/10 (was 6) · Speed 6/10 (unchanged) · Architecture 6/10 (was 5).**

Read-only. No source, test, config or stack was modified. No provider call, no stack contact, no
database access, no secret read. The only writes are this file and throwaway probe scripts in the
session scratchpad. Rows marked **Verified** were produced by executing the repository's own code in
`servers/fastapi/.venv` against the stored fixtures under
`artifacts/verbatim-design-measurements-20260911/fixtures/`. Rows marked **Inference** were not
executed.

Citation convention: `B/` = `servers/fastapi/`.

---

## 0. Verdict first

| switch | verdict |
|---|---|
| `AUTHORED_HTML_NORMALIZATION=tree` | **Enable in a gated series, pinned for the whole run.** The switch now does what it says. Do **not** flip it mid-flight: B4 is unfixed, so a resumed deck mixes representations under one `policy_revision`. |
| `AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING=true` | **Fix R4 (prompt-boundary `ValueError`) first**, then enable gated. The named acceptance defect (B11) is genuinely closed on the acceptance fixture; what is left is an uncoded failure path, not a content defect. |
| `AUTHORED_AUTHOR_STREAMING_ENABLED=true` (unblocked by T4's fix) | **Enable only on the chatgpt/codex transport.** The streaming call no longer dies, but it silently runs without its overload replay (R2) and its receipt does not say so. On the codex transport the replay was already unavailable, so R2 is inert there; on an openai-key transport it is a silent loss. |

Nothing below is a reason to hold the T4 provider change itself: it is strictly better than both the
pre-T4 and the post-T4 state.

---

## 1. Blocker 1 — placeholder markers drop content (B11 / A1). **Closed on the acceptance fixture.**

**Verified by execution.** Re-running `build_block_table` / `table_from_manifest` over the 43-page
contract manifest (`fixtures/manifest-43p/manifest.json`, 965 text blocks, 1,317 runs):

| measurement | result |
|---|---|
| blocks whose canonical text changed vs. the pre-fix construction | **52 / 965** |
| markers now kept | `㈜` ×45, `① ② ③ ④` ×7 — matches the commit message exactly |
| `projection.accepts(whole, canonical)` False under the new code | **0 / 965** |
| `projection.accepts(whole, canonical)` False under the old code (control) | 0 / 965 |
| `build_block_table` rejections (`source_placeholder_context`) | **0** |
| `㈜` occurrences in canonical text / in `fullText` | **45 / 45** |
| non-whitespace characters lost between `fullText` and canonical text, whole manifest | **0** (multiset difference over all 965 blocks is empty) |

The last row is the acceptance answer: on the fixture the design named as its bound acceptance
contract, canonical token text now preserves **every** non-whitespace source character. The
`㈜아이비즈소프트웨어` case from the first review round-trips.

**Does keeping markers ever make `accepts()` False?** No, and not by luck. `_span_pattern`
(`B/services/source_content/source_runs.py:176-196`) emits every marker token as
`(?:token\s*)?` — optional, at exactly the position the new code writes it — so a kept marker is
admitted and a dropped one is admitted too. Executed confirmation beyond the fixture: 26 hand-built
adversarial blocks (mixed `• ①` markers, `1.`/`1)` numbering, `㈀`/`⒈`/`❶`/`㉠` classes, bullets
crossing a pipe or an arrow, bracket-protected separators, marker-only blocks) plus a **4,000-case
fuzz** over generated marker/word/separator combinations: **0 `accepts()` failures, 0 rejections**.

**What is left (two narrow residuals, both Verified by construction, both 0 occurrences on the
acceptance fixture):**

- **R5 — marker-only *lines and pieces* still delete content.** The fix is inside
  `block_table.py`, but `project_source_runs` discards marker-only material earlier, and
  `_MARKER_ONLY` (`B/services/source_content/source_runs.py:57-60`) is built from
  `_ENUMERATORS`, which contains `㈀-㈜`. Executed:
  `"㈜\n아이비즈소프트웨어"` → `projection.text` = `아이비즈소프트웨어` (the `㈜` never reaches the
  block table at all); `"㈜ | 아이비즈"` → canonical `아이비즈` (the mark *and* the pipe are gone);
  `"①\n하나"` → `하나`; `"㈀\n내용"` → `내용`. Sites: `_drop_marker_only_lines`
  (`:270-276`) and `_drop_marker_only_pieces` (`:303`). Same narrowest fix as before — restrict the
  drop to `_ITEM_GLYPHS`-only material, and fold an enumerator-only piece into the following item
  instead of deleting it.
- **R6 — a mixed marker now carries its bullet glyph into published text.** Executed:
  `"• ① 혼합 마커"` → canonical `• ① 혼합 마커`, and `prompt_row()["markers"]` is `[]`.
  `_is_pure_item_glyph_marker` is all-or-nothing by design ("kept, even inside a mixed marker"), so
  a template that also draws a CSS bullet renders two. Low severity.

**One product-side note on the prompt change.** On the 43-page fixture the `markers` field is now
empty on **all 965 rows** (it previously reported 52). The contract sentence "markers are
presentation glyphs excluded from token text: render them as list/icon furniture if desired"
(`B/.../block_table.py:135-137`) therefore describes an always-empty field for this deck, and a kept
enumerator now arrives as ordinary text with no signal that it is an enumerator. A model that also
applies a CSS counter to the list would render `1. ① 하나`. **Inference**, not measured;
`accepts()` cannot see it either way because CSS-generated content is not DOM text.

**Verdict on blocker 1: closed.** R5/R6 are P3 follow-ups, not a reason to hold the switch.

---

## 2. Blocker 2 — tree normalization discarded when no rule fires (B1). **Closed.**

**Verified by execution** over the 199 stored `html-author.txt` fixtures, through the real
production entry point (`canonicalize_authored_slide_html` → `normalize_authored_html`, exactly as
`B/utils/llm_calls/author_slide.py:720-751` composes them):

| mode | adopted under the OLD rule (`if normalized.normalizations`) | adopted under the NEW rule (`if normalized.html != canonical`) | output bytes differ old vs new |
|---|---|---|---|
| `legacy` | 0 / 199 | 0 / 199 | **0 / 199** |
| `tree` | **0 / 199** | **199 / 199** | 199 / 199 |

So: **the byte-comparison adoption changes legacy-mode bytes nowhere on the 199 fixtures** — the
legacy normalizer is byte-identical on all 199 and fires zero receipts, so old and new agree
exactly. And tree mode went from never reaching the renderer to always reaching it, which is what
the switch was rolled out to deliver.

Supporting facts:

- Legacy cannot change bytes without a receipt by construction: every edit in
  `normalize_authored_html_legacy` (`B/utils/authored_html_normalize.py:130-196`) is paired with a
  receipt append in the same branch. **Verified by reading**, and consistent with the 199/199
  measurement.
- `B/utils/llm_calls/author_slide.py:723-751` is the only site that conditionally adopts HTML on a
  receipt count. Repo-wide grep of `.normalizations` outside `tests/` returns 8 other sites; all
  either extend a receipt list or serialize it. The commit's grep claim holds. **Verified.**
- **New bytes still pass admission.** Running the real `validate_authored_slide_html` over all 199
  fixtures with `AUTHORED_HTML_NORMALIZATION=tree` and `allow_illustration_slots=True`:
  **199/199 admitted**, zero `reject_hidden_overflow_declarations` or `admit_authored_html`
  rejections. The adoption does not introduce a policy failure on stored material. **Verified.**

**What the fix makes more urgent, not less:**

- **B4 is now a live rollout constraint, not a footnote.** Turning the knob on changes persisted
  bytes for 100% of slides instead of ~0%. `AdmissionContext.policy_revision` is still the constant
  `"authored-admission/v1"` (`B/services/authored_admission_policy.py:8`), and a repo-wide grep for
  `html_repr` still returns **nothing**. `get_authored_html_normalization()` is still read live per
  call. Flipping mid-flight now re-representations every remaining slide of an in-progress deck
  while the receipts claim one policy revision. Pin it per run.
- **B5 stands and is confirmed by the same data.** `expand_placeholders` parses and reserializes
  unconditionally (`B/services/source_content/placeholder/substitute.py:135,150`), and the 199/199
  tree result shows the serializer alone changes bytes on every real slide. So enabling the
  placeholder switch under `legacy` still adopts the canonical HTML5 serializer for every
  source-reflow slide. Step 3 of the rollout still silently contains step 2.
- The dead `normalization_profile` parameter is unchanged (`substitute.py:134`; `AdmissionContext`
  has no such field, so the profile is always `"document"`). **Verified.**

**Verdict on blocker 2: closed.** The switch now means what it says.

---

## 3. Blocker 3 — T4's widened provider guard (B2). **Closed, with one new seam.**

### 3.1 What was checked, and what came back

All four of the specific questions were reproduced against the real `provider_generate_call`, with
fake `llmai.chatgpt.client` / `llmai.openai.client`-shaped clients inside a running loop.

**Does the streaming path now return the generator with a pending reservation? — Yes. Verified.**
With a `ProviderAttemptReservation` published in `_PENDING`, the stream-shaped call
(`provider_generate_call(..., kwargs={"stream": True})`, no `outcome_scope` — the shape
`B/utils/llm_calls/author_slide_stream.py:327-334` builds) returned the generator and yielded all
its events. The first review's reproduction (`ProviderOutcomeError public_provider_empty_output`
before the first event) no longer occurs. Receipt:
`{physicalCallCount: 1, idempotencyCapable: False, replayUnavailable: None}`.

**Is the reservation truly consumed and not leaked? — Yes, on the paths that matter. Verified.**
`take_provider_attempt_reservations()` now has a production caller
(`B/utils/provider_idempotency.py:261`), and it runs in the caller's own task. Executed:
after one authored call, `current_provider_attempt_reservations()` is `()`; a second authored call
in the same task gets `idempotencyCapable: True, replayUnavailable: "attempt_reservation_unavailable"`
instead of silently inheriting the first slide's reservation. A generic call
(`provider_id="anthropic"`, no `outcome_scope`) made while a reservation is pending leaves it
untouched and keeps its own exception type.

Task-scoping was checked too: authoring batches run as separate tasks
(`asyncio.gather` / `ensure_future` in `B/services/authored_wave_scheduler.py:146,183`), and each
task gets its own `ContextVar` copy, so a leak inside one batch cannot reach another. The reserver
is awaited in the same coroutine as the provider call (`B/utils/llm_calls/author_deck.py:845-848`
and `:1586-1593`), so consumption is in the publishing context. **Verified by reading.**

**Did the physical-call bound survive? — Yes. Verified.** With an idempotency-capable transport and
three consecutive `503`s: exactly **2** physical calls, **1** replay, `reserve_retry` called once
with ordinal **2**, `cooldownWaitSeconds: 30.0`, then
`replayUnavailable: "physical_call_budget_exhausted"` and `public_provider_overload` raised.
`_MAX_OVERLOAD_PHYSICAL_CALLS = 2` (`B/utils/provider_overload.py:22`) and the deadline short-circuit
are unchanged; the refactor into `_replay_declined_reason()` is behaviour-preserving.

**Does the rescue still get its single bounded call on the codex transport? — Yes. Verified.**
Codex-shaped client, `outcome_scope="authored"`, reservation with `ordinal=3` pending, one `503`:
**1** physical call, no replay, `public_provider_overload` raised,
`{idempotencyCapable: False, replayUnavailable: "transport_idempotency_unconfirmed"}`, and the
reservation was consumed. B7 is unchanged in behaviour but is now **stated** in the receipt, which
was the review's actual complaint.

**Does the 429-regex false positive still authorize a replay? — No. Verified.**
`is_provider_overload(RuntimeError(status_code=400, "max_output_tokens must be <= 500"))` is now
`False`; the old predicate returned `True`. The named failure scenario (30 s burned on an identical,
certainly-failing replay) is gone.

### 3.2 The new seam, and a narrowing that was not intended

**R2 — P2. The reservation is consumed by a wrapper that is never invoked, whenever streaming is
on.** `B/utils/llm_calls/author_slide.py:1111-1122` builds
`provider_generate_call(..., outcome_scope="authored")` **unconditionally**, and only afterwards
checks `is_authored_author_streaming_enabled()` and discards it in favour of
`stream_author_call(...)`. The deleted code carried a comment that named this exact hazard
("Streaming authoring constructs a non-stream wrapper before selecting its actual stream wrapper.
Snapshot rather than consume so only the wrapper that is invoked can spend the retry reservation");
the commit removed the snapshot and the comment but not the wasteful construction.

**Verified by reproduction.** After building the authored wrapper, `_PENDING` is `()`. The discarded
wrapper's receipt reads `{physicalCallCount: 0, replayUnavailable: "transport_idempotency_unconfirmed"}`;
the stream call that actually runs reads `{idempotencyCapable: False, replayUnavailable: None}`.

Consequences: today, none — the codex transport had no replay to lose, and consuming a reservation
has no durable effect (the write happens only when `reserve_retry` is called). On an openai-key
transport with streaming on, the streamed call loses its one bounded replay **and its receipt says
`replayUnavailable: None`**, i.e. it reads as a call that never needed a replay. That is precisely
the misreading `be712c25e` set out to eliminate. Fix: build the wrapper inside the non-streaming
branch, or hand `stream_author_call` the already-built one.

**R3 — P2. Structured-first classification silently dropped the text signals for every llmai
error.** `B/utils/provider_overload.py:83-102` falls back to the free-text predicate only when
`provider_status_code(error)` is `None`. But every llmai error is a `BaseError` and always carries
an `int` `status_code` (`.venv/.../llmai/shared/errors.py:12-25`), so for llmai errors the fallback
is **unreachable** and only `_OVERLOAD_STATUS_CODES = {429,500,502,503,504}` decides. Measured
old-vs-new on constructed errors (**Verified**):

| error | old predicate | new predicate |
|---|---|---|
| `400 max_output_tokens must be <= 500` | True | **False** (this is the intended fix) |
| `404 model 500 not found` | True | **False** (also a fix) |
| `429 rate limit` | True | True |
| `503 overloaded` | True | True |
| `408 Request timed out.` | True | **False** (unintended) |
| `400 usage limit reached` | True | **False** (unintended) |

The 408 case is mostly covered upstream: `_is_call_timeout` (`B/utils/provider_idempotency.py:88-103`)
walks the `cause` chain and llmai attaches the original `httpx`/SDK timeout, and llmai maps real
timeouts to 504 anyway (`errors.py:139-141,206-212`). The `usage_limit_reached` case is the one
worth fixing: `usage_limit_reset_epoch` (`B/utils/provider_overload.py:261-279`) returns `None` when
the reset epoch is unparseable, and the error then falls through to `is_provider_overload`. With a
non-429 status it is now mapped to `public_provider_bridge_exception` with
`recovery_allowed=False` instead of `public_provider_overload`; `register_rate_limit`
(`B/utils/llm_calls/author_slide_admission.py:69-91`) reads `error.overload` and so registers no
cooldown, and the slide fails without its recovery call. **Inference** for the live probability
(no provider call was made); **Verified** for the predicate change.

Narrow fix: suppress only `_HTTP_OVERLOAD_PATTERN` when a structured status is present. Keep
`_RATE_LIMIT_PATTERN` and `_TIMEOUT_PATTERN` as unconditional positive signals — neither can
false-positive on `must be <= 500`, which was the whole motivating case.

The rest of the T4 architecture change is clean: `_exception_status_code` in
`B/utils/llm_utils.py:220-229` now delegates to `provider_status_code`, so the transient-retry
reader and the overload reader cannot drift apart. `provider_circuit_open` still classifies as
overload through the code path (`CircuitOpenError` is not a `ProviderOutcomeError`, so the fallback
runs). **Verified by reading.**

---

## 4. Lower-ranked items from the first review — which still stand

| item | status |
|---|---|
| **B3** — 429/5xx classification is a free-text match that authorizes a replay | **Substantially fixed.** Structured status is now the authority for replay authorization. Residual: R3 above, and the second classifier in `B/services/authored_checkpoint_errors.py:265-279` is untouched. |
| **B4** — no `html_repr`; `policy_revision` is a constant | **Stands, and matters more now.** `html_repr` grep still empty; `POLICY_REVISION = "authored-admission/v1"` unchanged. See §2. |
| **B5** — the two rollout switches are not independent | **Stands.** `substitute.py:135,150` unchanged; the 199/199 tree measurement confirms the serializer alone rewrites every real slide. |
| **B6** — quota pause vs. public deadline | **Stands.** Neither commit touched `workspace_operation_runner.py` or `ProviderQuotaPause`. |
| **B7** — replay inert on the production transport | **Stands by design, now disclosed.** The receipt states `idempotencyCapable: False` and `replayUnavailable: "transport_idempotency_unconfirmed"`. This is the right resolution of the complaint. |
| **B8** — expansion-induced raw-diagnostics violation escapes uncoded | **Stands.** `RawAuthoredHtmlViolation` (`B/utils/authored_html_tree/raw_diagnostics.py:39-45`) still has no `.code`; `B/services/authored_admission.py:224-231` still re-raises a `ValueError` without one. Reachable whenever `AUTHORED_HTML_NORMALIZATION=tree`, because the diagnose happens inside `normalize_authored_html_tree` before adoption — so the B1 fix did not change its reachability, but enabling tree does. One line to fix. |
| **B9** — self-imposed 64,000 bound, and `ValueError` out of prompt construction | **Stands.** `block_table.py:99-100` unchanged; `B/services/source_content/placeholder/prompt.py:22` still calls `placeholder_prompt_contract(build_block_table(...))` with no boundary catch. Re-measured post-fix: per-unit encoded table is 1,380–6,848 chars, median 4,484, total 176,788 for the 43-page deck — the size bound is far away, but `build_block_table`'s `reject()` path is not size-gated. **This is R4, and it is the one item I would fix before the placeholder switch.** |
| **B10** — contradictory prompt instructions | **Half stands; I overstated the other half.** Re-reading `B/utils/llm_calls/author_slide_rules.py:205-218`, the "copied VERBATIM from THIS slide's CONTENT" sentence governs **`data-illust-prompt` label strings**, i.e. attribute content, where tokens are forbidden anyway (`source_placeholder_context`). It is an ambiguity a loose reader could over-apply, not the direct contradiction I claimed. The real unresolved half is the asymmetry: `placeholder_prompt_for_slide` returns `""` when `current_session()` is `None` (`prompt.py:15-21`) while `placeholder_mode` decides token mode from the knob + context alone (`B/services/source_content/placeholder/text_step.py:18-22`). `4c0eb5a15` narrows the blast radius — its session-less admission path pins a context that declares no source reflow — but the asymmetry itself is unchanged. (`4c0eb5a15` separately fixed the *repair brief*'s contradiction, which is the other reviewer's B3.) |
| **C7** — `fullText` duplicated when identical to `text` | **Stands.** Re-measured: **764 of 965** rows now have `fullText == text` (the fix raised this, since canonical text is closer to the raw string). Total canonical 22,548 chars vs. `fullText` 22,940 — roughly 22k characters of the 177k table are an exact duplicate. |
| deadline is the fixed 360 s, not the operation deadline | Stands. |

Gates: `ruff check` is clean on all six touched files; every touched file is well under the 500-line
policy (`block_table.py` 152, `provider_idempotency.py` 493, `provider_overload.py` 289,
`author_slide.py` 1,251 — the last is a pre-existing frozen baseline exception and did not grow by
this change beyond 10 lines). The focused suites pass: **301 passed** across
`test_authored_provider_outcomes`, `test_authored_batch_policy`, `test_authored_provider_rate_limit`,
`test_authored_wave_scheduler`, `test_authored_author_stage_rescue`, `test_source_placeholder{,_prompt,_fixtures}`,
`test_authored_html_normalize`. The six new provider regressions and the three new
normalization/placeholder regressions are well-targeted; the streaming one in particular reproduces
the original defect shape rather than asserting the fix's implementation.

---

## 5. Remaining blockers, ranked

| # | P | file:line | scenario |
|---|---|---|---|
| R1 | P2 | `B/utils/authored_html_tree/raw_diagnostics.py:39-45` + `B/services/authored_admission.py:194-199,224-231` | (B8) Tree mode on. The post-expansion `validate_authored_slide_html` runs on expanded bytes; inserted source text crosses `MAX_TEXT_BYTES`/`MAX_HTML_BYTES`; `RawAuthoredHtmlViolation` carries `.diagnostics` but no `.code`, so `except ValueError` re-raises and the coded failure collapses into `authored_unexpected_failure`. One line: give it the first violation's code. |
| R2 | P2 | `B/utils/llm_calls/author_slide.py:1111-1122` | Streaming on. The authored wrapper is built, consumes the reservation, and is then discarded; the stream call runs without a replay and reports `replayUnavailable: None`. Inert on codex; a silent loss on an openai-key transport. |
| R3 | P2 | `B/utils/provider_overload.py:83-102` | A `usage_limit_reached` with an unparseable reset and a non-429 status is no longer classified overload, so no cooldown is registered and the slide fails without its recovery call. Keep `_RATE_LIMIT_PATTERN`/`_TIMEOUT_PATTERN` unconditional; suppress only `_HTTP_OVERLOAD_PATTERN` when a status exists. |
| R4 | P2 | `B/services/source_content/placeholder/prompt.py:22`, `B/.../block_table.py:76-77,99-100` | Placeholder on. `build_block_table`'s `reject()` or the 64,000-char `ValueError` propagates out of *prompt construction*, where the author stage's error envelope does not expect a source-readback error. Catch at the prompt boundary and map to a coded author failure. |
| R5 | P3 | `B/services/source_content/source_runs.py:57-60,270-276,303` | A line or piece made only of an enumerator or `㈜` is still deleted before the block table sees it (`"㈜\n아이비즈"` → `아이비즈`). 0 occurrences on the acceptance fixture. |
| R6 | P3 | `B/services/source_content/placeholder/block_table.py:52-63` | A mixed marker (`• ①`) keeps its bullet glyph in published text and reports nothing in `markers`. 0 occurrences on the acceptance fixture. |
| R7 | P2 (unchanged) | `B/services/authored_admission_policy.py:8`, `B/services/authored_admission_context.py:70-72` | (B4) No `html_repr`, constant `policy_revision`. Now that tree mode changes 100% of slides, a mid-flight flip mixes representations under one receipt identity. Pin the mode per operation, or pin the run. |
| R8 | P2 (unchanged) | `B/services/source_content/placeholder/substitute.py:135,150` | (B5) The placeholder switch adopts the canonical serializer even under `legacy`. Confirmed by the 199/199 measurement. |

---

## 6. What must still be verified live

Items 1, 2, 5 and 6 of the first review's live list are unchanged. Two are now answerable offline and
two change shape:

1. **Tree mode on fresh output.** Now worth running: 199/199 stored slides change digest and all 199
   still admit, so the remaining unknown is downstream — Chromium capture, the Hybrid text-run
   assembler, PPTX export — on *freshly authored* bytes, not stored ones.
2. **Token mode on the 43-page fixture.** The `㈜` question is answered offline (45/45 preserved in
   canonical text, 0 characters lost across 965 blocks). What still needs a live run is the
   *model-side* half: what fraction of first attempts fail `source_placeholder_missing`, and whether
   any deck trips R4's uncoded prompt-construction failure.
3. **Streaming × placeholder × tree together** — unchanged, and R2 means the streaming leg should be
   run on the codex transport only until `author_slide.py:1111` is fixed.
4. **A real 429 receipt** — now more informative than before, because the receipt names
   `replayUnavailable` instead of leaving a never-replayed call ambiguous.

---

## 7. Scoring rationale

- **PPT quality 6 → 7.** The one defect that made published bytes wrong while every gate said
  admitted is closed on the named acceptance fixture, measured to zero lost characters. Not 8
  because R5/R6 are unclosed (narrow, zero prevalence on that fixture but real), the
  enumerator-as-text rendering risk is unmeasured, and no end-to-end deck has been produced under
  either switch.
- **Speed 6 → 6.** Nothing here moves the needle. Tree mode's ~11 ms per admission now applies to
  100% of slides instead of ~0%, still noise against 21.4 s/slide. The block table shrank by 344
  characters on the 43-page deck (176,788 vs. 177,132) — within noise; the ~22k characters of
  duplicated `fullText` (C7) are untouched, and the fixed 360 s deadline is untouched.
- **Architecture 5 → 6.** Three of the five structural defects are fixed at the right level: the
  representation switch is real (B1), `outcome_scope` is the sole authority again with a documented
  take-once reservation lifetime (B2), and overload classification is structured-first behind one
  shared status reader (B3). B4 and B5 are untouched, and R2 adds one new wart. The distance to 8 is
  still five small, well-localized fixes — R1, R2, R3, R4 and R7 — not a redesign.

---

### Verification performed for this re-review

Executed in `servers/fastapi/.venv` (read-only, offline): the focused unit suites for T3/T4/T5
(**301 passed**); block-table construction over the 965 text blocks / 1,317 runs of
`fixtures/manifest-43p`, with old-vs-new canonical text diffing, `projection.accepts` on both,
per-character multiset loss accounting, marker histograms, per-unit encoded table sizes and
`fullText` duplication counts; 26 hand-built adversarial marker blocks plus a 4,000-case fuzz over
generated marker/separator/word combinations; `normalize_authored_html` in both modes plus the real
`validate_authored_slide_html` over the **199** stored `html-author.txt` documents, comparing
receipt-gated versus byte-gated adoption and re-running admission on the adopted bytes; and
reproductions of the streaming call, the reservation lifetime, the two-physical-call bound with an
injected reserver, the codex rescue path, and the overload-classification table, against the real
`provider_generate_call` with fake `llmai.chatgpt`/`llmai.openai`-shaped clients. `ruff check` on the
six touched files. No provider call, no stack contact, no database access, no secret read, no source
or test modified.
