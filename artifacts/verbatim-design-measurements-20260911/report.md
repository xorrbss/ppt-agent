# Verbatim placeholder-authoring design — section 3 measurements

Date: 2026-09-11. Design: [`docs/authored-verbatim-placeholder-authoring-design-20260911.md`](../../docs/authored-verbatim-placeholder-authoring-design-20260911.md)
rev 3 (decision `.spec-memory/decisions/authored.verbatim-placeholder-authoring.1.json`).

Read-only throughout: stored slide HTML, stored source manifests, stored provider-usage
receipts, stored checkpoints. **No generation, no provider call, no database write, no stack
contact.** Scripts and per-row JSON are beside this file.

---

## 3.1 Emphasis inside a projected run — **character-range tokens ARE needed**

The question is not whether a block is split across elements (run tokens already express that)
but whether a styled span sits **inside one projected run**, which no run-level token can
address. Measured over the five published verbatim decks (runs 11, 15, 16, 20, 21 — 175 slides).

Method: every element carrying `data-source-block-id` is parsed out of `slides.html_content`;
the value is split into `(block, run_start, run_end)` exactly as
`services/source_content/source_runs.py::parse_source_binding_id` does. For each emphasis
descendant covering only part of that element's text, the block's raw text is taken from the
stored manifest, projected with the pipeline's own `project_source_runs`, and the emphasised
substring is compared against **every contiguous run span** of that projection (including the
`accepted_texts` marker-stripped variants).

| | count |
|---|---|
| source-bound elements (5 decks) | 1,817 |
| distinct source blocks bound, per deck | 289 (1,445 deck-block pairs) |
| blocks the binder split across elements (`:run:` bindings), per deck | 57 (123 deck-block pairs) |
| partial-emphasis spans examined (5 decks) | 125 |
| **aligned with a contiguous run span** (run tokens suffice) | **24** |
| **cutting inside a run** (needs a character range) | **101** |

The 101 are spread over 37 distinct blocks, and **63 of them are in single-run blocks**, where
no run split exists to align with at all. Emphasis kinds: `<strong>` 95, inline `color` 5,
`<em>` 5. Examples (emphasised chars / block chars):

| deck · slide | block | runs | span | emphasised text |
|---|---|---|---|---|
| run-11 s1 | `source-slide:2:text:4` | 1 | 11/23 | `'사업이 굴러가는 구조'` |
| run-11 s2 | `source-slide:3:text:4` | 1 | 4/21 | `완성한 것` |
| run-11 s3 | `source-slide:4:text:4` | 1 | 2/30 | `필수` |
| run-11 s12 | `source-slide:13:text:8` | 4 | 9/56 | `궁금증 · 문제 · 반전` |

**Verdict: the bounded character-range form `{{q:<id>@<start>-<end>}}` is required.** The design's
stated expectation — "projected runs follow delimiters, not style boundaries" — is confirmed, and
that is precisely why run tokens cannot carry this emphasis. Without character ranges, 101 spans
across 37 blocks would lose their emphasis or force the model to emit text.

---

## 3.2 Negative fixtures from the failed runs

The six failed runs (13, 14, 17, 18, 19, 23) of the same 35-slide fixture. Their directories
hold no slide HTML, but `authored_slide_checkpoints` does: for the two runs whose job id the
artifacts record, 35 checkpoint rows each with 34 and 32 `author_html` values respectively, so
**the failing HTML is recoverable** and these can be real negative fixtures rather than a paper
table.

| run | recorded failure | predicate in the artifact | job id | checkpoint HTML |
|---|---|---|---|---|
| run-13 | `authored_slide_author_failed` | 1 visibility failure, 1 missing block id | not in artifacts | recoverable by time correlation |
| run-14 | `source_content_browser_readback_failed` | `09-readback-failure.json` carries no block list | not in artifacts | same |
| run-17 | `source_content_browser_readback_failed` | `source-slide:10:text:17` — integrity **and** visibility failure, 0 order violations | not in artifacts | same |
| run-18 | `authored_slide_author_failed` | author stage gave up | `fca17341` | **35 rows, 34 author_html** |
| run-19 | `source_content_browser_readback_failed` | `source-slide:34:text:6` | `0e66d677` | **35 rows, 32 author_html** |
| run-23 | `source_content_browser_readback_failed` | `source-slide:2:text:6` | not in artifacts | recoverable by time correlation |

### What must catch what, and where

Sections 2.1 and 2.2 do not exist yet, so this is a specification of the stage each failure must
move to — not a replay result.

| today's failure | under the design it must become | stage | coded rejection |
|---|---|---|---|
| readback **integrity** failure on one block (run-17, 19, 23) | impossible by construction: the server substitutes the text, so "where did the model put it" is not asked | — | none; the slide is admitted |
| model omitted / altered a block | a static check before any Chromium work | 2.1 static checks | `source_placeholder_missing` |
| model emitted a block twice | same | 2.1 static checks | `source_placeholder_duplicate` |
| model invented an id | same | 2.1 static checks | `source_placeholder_unknown` |
| token in an attribute, `<style>`, comment, or hidden content | same | 2.1 static checks | `source_placeholder_context` |
| author stage exhausted its budget (run-13, 18) | unchanged: a flagged placeholder is still the answer | author rescue | existing `author:attempts_exhausted` |
| expansion pushes the document past a resource limit | after substitution, before render | 2.1 re-check | renderer-policy rejection |

**The invariant to hold in review: none of these six may turn into a pass.** A historical binder
failure that becomes an admitted slide is only correct where the *binding* was the thing that
failed (run-17/19/23); where the model's output was actually wrong, the new static check must
reject it with a code, and where authoring itself gave up (run-13/18) the outcome must stay a
flagged placeholder.

---

## 3.3 Token accounting — receipts **do** exist, and the saving is small

The design records "Current run directories carry no usage receipts". True of the directories —
and **`provider_usage_receipts` holds 17,699 rows**, with `input_tokens`, `output_tokens`,
`stage`, `operation` and `model` per call. So the baseline is measured, not absent.

Author-stage output tokens for 35-slide verbatim jobs (12 most recent):

| job | state | author output tokens | author input tokens | author calls |
|---|---|---|---|---|
| `32de70c4` | failed | 125,862 | 498,417 | 49 |
| `2246ddb8` | failed | 128,422 | 512,485 | 48 |
| `58fe371f` | published | 123,261 | 491,600 | 45 |
| `999f04be` | published | 115,660 | 438,796 | 41 |
| `9bc815e1` | published | 110,163 | 431,740 | 41 |
| `89cf6fc9` | published | 114,955 | 491,553 | 42 |

Range 110k–128k output tokens per deck, 40–49 author calls.

Now the part that changes the premise. The fixture's 289 text blocks hold **5,693 characters of
source text**; the same blocks as placeholder tokens (`{{q:source-slide:NN:text:NN}}`) come to
**8,113 characters — 42.5% more**. Korean source blocks here are short (mean ≈ 20 chars) while a
block id plus wrapper is ≈ 28 chars.

Characters are not tokens, and the two sides tokenize very differently: Hangul runs roughly
0.7–1.1 tokens per character in modern BPE vocabularies, structured ASCII like
`source-slide:30:text:6` roughly 0.25–0.35. On those published ranges the 5,693 Hangul characters
are ≈ 4,000–6,200 tokens and the 8,113 id characters ≈ 2,000–2,800, so token mode probably does
save — **on the order of 1,500–3,500 tokens, which is 1–3% of the 110k–128k a deck already
emits.** That is an estimate from published token-density ranges, not a measurement: **no
tokenizer for the production model is installed in this venv** (`tiktoken` absent), so the exact
delta cannot be computed here.

**Conclusion: token cost is not a reason to adopt placeholder authoring.** The output is
dominated by the HTML and CSS the model writes, not by the source text it repeats; the case for
the design is correctness, and section 3.3 should say so rather than lead with a saving.

### To make the number exact

One field pair on the E4 receipt, recorded per author call, settles it without a tokenizer
dependency: `sourceTextOutputTokens` (tokens the call spent emitting source-owned text) and
`placeholderOutputTokens` (tokens spent on ids and wrappers). Both are computable by the provider
adapter, which already holds the tokenization it billed. With those, the first token-mode series
produces the delta directly against the rows above.

---

## 3.4 The 43-page / 96-image contract fixture — **found, exact match**

`source-content.template-restyle-contract@7` binds "원본 43쪽의 텍스트와 이미지 96개 출현".
A stored manifest matches both counts exactly:

| | value |
|---|---|
| source digest | `sha256:32b098831a847…` |
| reference snapshot | `2fb9487a…` |
| units (pages) | **43** |
| `image` blocks (occurrences) | **96** |
| text blocks | 965 |
| source text characters | 22,940 |
| other blocks | `shape` 491, `source_note` 28, `chart` 3, `table` 2 |

For scale against the current fixture: 43 pages versus 35, 965 text blocks versus 289, 22,940
source characters versus 5,693 — **four times the text of the deck all current acceptance
evidence rests on.** The contract's forbidden list (no native table/chart preservation, no reuse
of source shapes or layouts, no image dedupe/hiding/off-canvas) makes the 3 charts and 2 tables
in this manifest a deliberate exclusion test, which the 35-slide fixture cannot exercise.

Added as a fixture candidate. The source document itself was not located on disk in this pass;
the manifest and its snapshot id are enough to re-derive it through the normal
`/references/snapshots` path.

---

## Per-slide fixtures for the gate work (T2)

`fixtures/<run>/<slide-index>/` carries one directory per slide, exported by
`export_verbatim_fixtures.py` (read-only; no generation, no provider call, no database write,
no secret value in the output). 253 slide directories, 1,065 files, 7.7 MiB.

| file | content | when absent |
|---|---|---|
| `manifest.json` | that slide's source unit and its blocks | no unit for the index |
| `projection.json` | `project_source_runs` per text block: runs, markers, char spans, lines | same |
| `html-render.txt` | the published bytes (`slides.html_content`) | deck did not publish |
| `html-author.txt` | checkpoint `author_html` | null — **cleared after publication, so published decks have none** |
| `html-illustrated.txt` | checkpoint `illustrated_html` | null |
| `verdict.json` | job id and the basis for it, job state, checkpoint status/failed stage/error, what the run directory recorded, and an `absent` list naming every artefact that does not exist | — |
| `metrics.json` | **never produced** | browser metrics are not persisted anywhere: `authored_slide_checkpoints` has no metrics column, `artifact_refs` is null, and `authored_evidence_*_v3` are empty |

Coverage: 253 slide directories, each tagged with a `fixture_role` so a consumer need not
re-derive why it is there.

| role | slides | what it is |
|---|---|---|
| `published_slide` | 175 | all 35 slides of each published deck (runs 11, 15, 16, 20, 21) — final bytes only |
| `authoring_passed` | 66 | runs 18 and 19 in full: every slide whose authoring output survived |
| `failing_slide` | 12 | run-13 `22,29`; run-14 `02`; run-17 `09`; run-18 `30`; run-19 `02,12,33`; run-23 `01,08,11,29` |

**Runs 18 and 19 are the only stored model output for the "model output → static checks" path**,
because a published deck's `author_html` is cleared once it publishes. They are therefore exported
in full: 34 and 32 slides with `html-author.txt` (30 and 32 also with `html-illustrated.txt`).

A failing slide is any slide the record names: an explicit slide index, the slide owning a failing
source block (mapped through the manifest), a checkpoint with a failed stage, or a slide that
produced no authoring output while its neighbours did. **That last case is why a `failing_slide`
directory can hold no HTML at all** — run-18 `30` and run-19 `02, 12, 33` never authored, which is
exactly what makes them negative fixtures; they keep their manifest, projection and verdict, and
`absent` names each missing artefact.

Job ids are resolved authoritatively: each run directory's `05-admission.json` records an
`operation_id`, which joins to `presentation_generation_jobs.workspace_operation_id`. **All
eleven runs resolve, and all eleven agree with the `created_at` ordering this replaces**, so the
fixture contents are unchanged by the correction — only the recorded basis is. Each
`verdict.json` now carries `workspace_operation_id` alongside `job_id` and states the join it
came from, so the mapping is checkable rather than asserted.

### The 43-page contract manifest, exported (T5)

`fixtures/manifest-43p/` carries the contract fixture itself, by
`export_43p_manifest.py`: `manifest.json` (all 43 units and their blocks, 2.0 MiB),
`projection.json` (`project_source_runs` for each of the 965 text blocks, keyed by
`sourceBlockId` and carrying the unit it belongs to, 0.5 MiB) and `README.json` with the
provenance. The export refuses to write if the counts are not 43/96 or if either payload
matches a secret-shaped pattern.

Projection over the 965 text blocks: **1,317 runs, 225 blocks with more than one run** — a
quarter of the blocks are multi-run, against 57 of 289 in the 35-slide deck.

**The choice of row had to be made deliberately.** That `source_digest` has **35 manifest rows,
each with its own `manifest_digest`**, so there is no single stored manifest to point at. Grouped
by text payload (block ids and their text) they fall into two:

| group | rows | window | meaning |
|---|---|---|---|
| `d3839b142c32` | **31** | 2026-08-27 18:42 → 09-02 07:31 | current id assignment, identical across all 31 rows |
| `1ae301686e36` | 4 | 2026-08-27 15:43 → 17:16 | superseded assignment: 450 ids exist on only one side, and 425 shared ids carry *different text* |

The export takes the latest row of the 31 (`manifest_digest sha256:6f25a000…`, snapshot
`0c6d0a0e…`, analysed 2026-09-02 07:31) and records the superseded group in `README.json`. All 35
rows agree on 43 units, 965 text blocks and 96 image blocks, so the contract match is robust; only
the block identity differs, and it stopped changing after 2026-08-27 17:16.

## Limits of this pass

- 3.2's job ids are resolved for all eleven runs through the recorded `operation_id`, and the
  checkpoint HTML is exported.
- No browser metrics exist to export, so a gate that needs them cannot be tested against these
  fixtures without re-rendering.
- 3.3's token delta is an estimate with a stated range, not a measurement, because the
  production tokenizer is not installed. The measured half (receipt totals) is exact.
- 3.1 excluded bound blocks whose text is not in a stored manifest (the pooled manifest set
  covered every block the five decks bound, so nothing was dropped for the verdict).
