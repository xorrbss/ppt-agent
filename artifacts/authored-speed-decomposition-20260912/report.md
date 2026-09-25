# Where the seconds go: a measured stage decomposition of authored generation

- Date: 2026-09-12. Offline, stored evidence only. No stack contact, no generation, no
  provider call, no knob write, no source file touched. Every database statement ran
  read-only through the sanctioned wrapper (`run_speed_aggregation.ps1`, shaped after
  `servers/fastapi/scripts/run_authored_checkpoint_repair_probe.ps1`): the secret is
  decrypted into the child process's memory and never printed or persisted.
- Asked for by the orchestrator after two implementation reviews scored speed 6/10 and
  said the commits do not move it
  (`artifacts/impl-review-20260911/rereview-gate-and-loop.md`,
  `rereview-normalization-provider-placeholder.md` §7).
- Reproduce: `artifacts/authored-speed-decomposition-20260912/run_speed_aggregation.ps1
  -Out artifacts/authored-speed-decomposition-20260912/speed-decomposition.json`.
  Per-deck records are in that JSON.

## 1. What could be measured, and what could not

| source | covers | period |
|---|---|---|
| `provider_usage_receipts` | one row per logical call: stage, latency, input/output/cached tokens, retry class, outcome | **2026-08-18 → today** |
| `presentation_generation_jobs` | each deck's wall clock (`published_at − last_started_at`), slide count, state | same |
| worker log `[authored] done` line | the pipeline's own split: total, pipeline, author thread-seconds, render, publication, persist, export | 2026-09-04 → today only |

**Not measurable from any stored source, stated plainly:**

- **Admission and HTML normalization.** They emit no usage receipt and no log field. The
  ~11 ms per admission in the re-review is that reviewer's own measurement, not something
  this decomposition can confirm or refute.
- **Queue wait** before a job starts, and any time between attempts.
- **August's stage split.** The oldest surviving worker log is 2026-09-04, so for August
  only the receipt-derived stages and the deck wall exist. Nothing here reconstructs
  August's render or publication time.
- One log field was **renamed** (`author=` → `author_sum=`, with `profile=` and
  `publication=` added). Any tool keyed on the old spelling silently drops every deck
  since the rename: 24 of the 27 surviving lines. This report parses both.

## 2. The paired comparison

Confounds controlled: same deck size band, same provider and model family
(`gpt-5.6-sol` + `gpt-image-2`), vision QA off in both, published decks only. Deck size
had to be controlled because August's median deck is 43 slides and September's is 8, and
per-slide cost falls sharply with deck size.

**5–10 slide decks — August n=15, September n=8**

| | August | September | ratio |
|---|---|---|---|
| **wall per slide** | **23.4 s** | **79.2 s** | **3.4×** |
| author thread-seconds per slide | 180.6 s | 266.3 s | 1.47× |
| effective concurrency (author thread-s ÷ wall) | 7.6 | 4.2 | 0.55× |
| author call p50 | 73.6 s | 163.6 s | 2.22× |
| author calls per slide | 2.00 | 1.12 | 0.56× |
| input tokens per author call | 2 647 | 7 648 | 2.89× |
| output tokens per author call | 1 680 | 4 214 | 2.51× |

31+ slide decks cannot be paired: every September deck in that band ran a different model
(`luna`/`astra`), so the comparison would measure the model, not the pipeline.

**Per-stage thread-seconds per slide, same cohort** (a stage absent from a period had no
receipts at all there):

| stage | August | September | Δ |
|---|---|---|---|
| author | 180.56 | 266.27 | **+85.71** |
| header | — | 5.68 | **+5.68** |
| publication | 0.16 | 4.38 | **+4.21** |
| render (capture) | 1.60 | 5.03 | **+3.43** |
| quality | — | 0.34 | +0.34 |
| checkpoint / persist / export / illustrate | 0.08 | 0.22 | +0.14 |
| repair | 0.00 | 0.00 | 0.00 |
| outline | 16.16 | 6.88 | **−9.29** |
| **total thread-seconds per slide** | **198.6** | **288.8** | **+90.2** |

Repair is 0.00 in both: on these decks the repair stage made no provider call at all.

## 3. Where the 55.8 extra seconds per slide come from

Wall per slide is almost exactly author thread-seconds divided by effective concurrency,
plus whatever runs outside that: August 180.6 ÷ 7.6 = 23.8 against a measured 23.4, and
September 266.3 ÷ 4.2 = 63.4 against a measured 79.2. A two-factor decomposition of the
author term, splitting the interaction evenly:

| cause | seconds per slide | share of the +55.8 |
|---|---|---|
| **1. Effective concurrency fell 7.6 → 4.2** | **+23.8** | 43 % |
| **2. Author thread-seconds grew 180.6 → 266.3** | **+15.8** | 28 % |
| **3. Work outside the author term: new header call, publication, capture** | **+15.8** | 28 % |

### 3.1 Concurrency — the largest single cause, and the one with a known lever

`services/authored_provider_rate_profile.py:43` sets `_TOKEN_STEADY_WAVE_CONCURRENCY = 2`
for the token profile, which is the profile every `codex` deck uses
(`_TOKEN_PROVIDER_IDS = {"codex"}`, `:33`, consulted at `:105`). The author pool defaults to 5 and caps at 8,
but the wave gate admits two calls at a time unless
`AUTHORED_BATCH_WAVE_CONCURRENCY` overrides it (`:145-147`). The file's current shape
landed 2026-09-05 (`c7b6383c1`); August decks predate it and ran at a measured effective
concurrency of 7.6–15.0.

**Expected saving, if the knobs are set:** at wave 8 the same 266.3 author thread-seconds
per slide become 266.3 ÷ 8 = 33.3 s of wall instead of 63.4 s, i.e. **−30 s per slide**.
This is not a new estimate: the 2026-09-08 measurement recorded in memory
`authored-speed-config-20260908` ran the same fixture at `AUTHORED_AUTHOR_CONCURRENCY=8`
plus `AUTHORED_BATCH_WAVE_CONCURRENCY=8` with repair on and got 1 766 s against the
default's 2 910 s, **zero defective slides, no provider 429 in any run**. That is a
1.65× deck-level win already measured, on a knob nobody has turned on.

Caveat worth stating: the wave gate exists because the token profile is a personal quota
rather than a billed API. The 09-08 runs found no 429, and memory `codex-429-not-a-blocker`
records the owner's decision not to design around that risk, but the quota is shared with
whatever else runs on the machine.

### 3.2 Author thread-seconds — partly ours, mostly not

Input tokens per call grew 2.89× and output tokens 2.51×; call p50 grew 2.22×. The prompt
growth is real and documented independently
(`docs/authored-author-prompt-profiles-design-20260912.md` measures the assembled prompt
at 8 623 → 12 194 characters, and the style anchor adds up to 6 400 more per call).

- **Fixable, bounded:** the `august-0819` prompt profile cuts the assembled prompt by
  18.7 % on the default style. If latency scaled linearly with input tokens — it does not —
  that would be worth roughly 6 s per slide here. The A/B in
  `artifacts/authored-prompt-profile-ab-20260912/PROTOCOL.md` will measure it rather than
  assume it.
- **Not fixable by us:** output tokens grew 2.5×, which is the model writing more markup
  per slide, and the model's own latency per token. Calls per slide actually *fell*
  (2.00 → 1.12), so this is not extra calls; it is longer calls.

### 3.3 Work outside the author term

The `header` stage did not exist in August and now costs 5.68 thread-seconds per slide;
`publication` grew 0.16 → 4.38; capture grew 1.60 → 5.03. Against that, `outline` fell
16.16 → 6.88. Net **+4.5 thread-seconds per slide**, and because these run at low or no
concurrency the wall cost is close to the thread cost, which is why the residual in the
table above is larger than the thread delta alone.

The deck-level fixed cost is visible separately in the log: `total − pipeline` is 30–40 s
on the oldest surviving decks but 267–319 s on several recent ones. Amortized, that is
**5.2 s per slide on a 35-slide deck and 33.5 s per slide on an 8-slide deck** — a
three-slide deck spends 136.7 s per slide in total against 43.8 s inside the pipeline. Most
small-deck testing is therefore measuring deck setup, not per-slide authoring.

## 4. What this says about the two reviews

Both reviews are right that the commits they examined do not move speed, and this
decomposition says why: none of them touches concurrency, prompt size, or the deck-level
fixed cost, which are the three terms that carry the regression. Tree normalization at
~11 ms per slide is, as the re-review says, noise against a 79 s per-slide wall.

## 5. Ranked answer to "why is it slower than August"

| # | cause | measured cost | fixable? |
|---|---|---|---|
| 1 | Wave concurrency capped at 2 for the codex token profile | +23.8 s/slide | **Yes** — two existing knobs; 1.65× already measured on 2026-09-08 with zero defects |
| 2 | Author call grew: input 2.9×, output 2.5×, p50 2.2× | +15.8 s/slide | **Partly** — prompt profile trims 18.7 % of the prompt; output length and model latency are not ours |
| 3 | New header call, publication and capture growth, plus deck-level setup | +15.8 s/slide, and 33.5 s/slide of deck fixed cost on an 8-slide deck | **Unknown** — no one has measured what the deck setup is spending; it is the largest unexamined block |

**The first thing to do is not a code change.** Set the two concurrency knobs on a
measurement run and confirm the 30 s per slide; that is the only item here with a prior
measurement behind it. The second is to instrument deck-level setup, because on the deck
sizes this programme actually tests, it is bigger than everything §3.3 names put together
and nothing records what it is.

## 6. Honest limits of this report

- Eight September decks and fifteen August decks in the paired cohort. Medians, not means;
  no confidence intervals are claimed.
- The two sources agree where they overlap (September 8-slide decks: 79.2 s/slide from the
  jobs table, 76.0 s/slide from the log), which is the only cross-check available.
- Every "expected saving" in §3 and §5 is labelled as an expectation except the 2026-09-08
  concurrency figure, which is a measurement someone else took on a different fixture.
- The August cohort's decks were authored under a different prompt, a different rate
  profile and different gates. This report attributes the difference to stages, not to
  commits; it does not claim which commit caused which second.
