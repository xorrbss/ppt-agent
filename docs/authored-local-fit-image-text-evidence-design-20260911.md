# Image-text evidence: one producer for a field that has two readers and none

- Date: 2026-09-11
- Status: **design note only.** No code changes accompany it.
- Problem evidence: [`artifacts/local-fit-source-reflow-replay-20260911/report.md`](../artifacts/local-fit-source-reflow-replay-20260911/report.md),
  section "Baseline on non-verbatim decks", commit `7b25d19dd`.

## 1. The defect

`image_text_evidence` is read in two places and written in none.

| reader | channel | shape it expects |
|---|---|---|
| `utils/slide_capture_fit_runner.py::_imageTextCheck` | the **policy**, as `P.image_text_evidence` | a list or object of records, each matched by `qid` **or** by `asset_digest`/`identity`, carrying either (`text_free: true` **and** `review_digest`) or (`measurement_identity` **and** `minimum_text_pt >= floor_pt`) |
| `services/authored_local_fit_evidence.py::_validate_image_text_scope` | the **browser metrics**, as `elements[qid].image_text_evidence` | a mapping with `status` in `{"no_text", "font_floor_verified"}` |

Note that these are two different channels with two different shapes. A design
that fills only one leaves the other failing closed.

`local_fit_policy()` does not set the key, and the production pipeline calls that
function with exactly the arguments an offline measurement does
(`services/authored_presentation_generation.py:645`,
`services/authored_presentation_pipeline.py:937`). Nothing else sets it either.

### What it costs

Measured over 60 non-verbatim authored decks, 1,252 slides:

| outcome | slides |
|---|---|
| `unresolved`, reason `font_unverifiable`, **zero candidates produced** | 414 |
| of those, carrying a raster element | 401 |
| slides without that reason that nonetheless carry a raster element | 4 of 838 |

Attribution was checked rather than assumed. `font_unverifiable` has two
producers in the runner, a failed baseline measurement and the image-text check.
On a sample of eight affected slides the report's own `image_text` records showed
`scope: "unverifiable"` in all eight, and `baseline.complete` was `true` in seven
of the eight. The image-text check is the producer.

Images are present when the fit runs. `_author_illustrate_render_pipeline` builds
`illustrated_html` through `illustrate_slide` and then hands **that** document to
`local_fit_policy(...)` (`authored_presentation_pipeline.py:934-943`). There is no
ordering in which the fitter sees a slide before its pictures.

### What filling it would restore

Simulated by handing the runner the records it asks for, in-process only, on five
affected slides:

| slide | before | after |
|---|---|---|
| DEVAX PLATFORM s6 | `unresolved`, 0 candidates | `unchanged`, no defect |
| 아이비즈 s28 | `unresolved`, 0 candidates | `unresolved` / `candidates_exhausted`, 54 candidates |
| 현대백화점 s15 | `unresolved`, 0 candidates | `unchanged`, no defect |
| 현대백화점 s10 | `unresolved`, 0 candidates | **`spacing_adjusted`, repaired** |
| 현대백화점 s18 | `unresolved`, 0 candidates | `unresolved` / `candidates_exhausted`, 57 candidates |

So the gap is the whole blocker: with evidence present the search runs normally,
and at least one slide in five was repairable all along.

**That simulation is not the proposal.** It declared every image `text_free:
true`, which is precisely what production must never do. It establishes only that
the field is the blocker.

## 2. Design

### 2.1 One producer, at materialization

The status is issued **where the image enters the document**, by
`services/authored_illustration_stage.py::illustrate_slide`, which is already the
single function that returns a slide's finalized HTML and is already the only
place that knows each picture's provenance. It gains one additional return value:
a tuple of evidence records for the images it placed.

No other stage issues evidence. A picture that reaches the DOM without passing
through this stage has no record, and therefore stays unverifiable.

### 2.2 Provenance decides the status, and nothing else looks at pixels

`illustrate_slide` already distinguishes three origins through its own
parameters. Each maps to one status, decided from data the stage already holds:

| origin | how the stage knows | status | basis |
|---|---|---|---|
| Server-composed figure | `infographic` / `infographic_assets` | `no_text` | The figure's text is authored as DOM text beside the asset; the asset itself is an icon or cutout from the pack. The pack's manifest already declares this, and the runner's `review_digest` is the asset's own digest. |
| Generated illustration | placed via `image_service` | `no_text` | The request is issued by this fork with a prompt that asks for no lettering. The record cites the generation request identity as `review_digest`. |
| Source image the user supplied | `source_images`, carried on `data-asset-id` | `font_floor_verified` **only with a measurement**, else no record | A picture out of the user's own deck may contain any lettering at any size. It is not text-free by construction and must not be declared so. |
| Anything else | no provenance | **no record** | Fail closed, exactly as today. |

The fourth row is the point of the table. The rule is not "images are fine"; it
is "an image whose origin this system controls has a known text property, and
one whose origin it does not control keeps the current refusal".

### 2.3 No new judge

Nothing in this design inspects an image to decide whether it contains text.
There is no OCR step, no vision call, no new heuristic and no new threshold.
Every status is a restatement of a fact the pipeline already recorded when it
asked for or accepted the asset. The two existing judges keep their jobs:

- the runner still decides `verified_no_text` / `verified_text_size` /
  `unverifiable` by the same predicates it uses today;
- `_validate_image_text_scope` still refuses a visible raster whose element
  carries no admissible status.

Neither predicate is edited. The change is that a producer now exists to satisfy
them.

### 2.4 How the record reaches both readers

One record set, two hops, no second source of truth.

1. `illustrate_slide` returns records keyed by **`qid`**.
2. The pipeline puts them on the policy it already builds for the slide, as
   `image_text_evidence`, at the single call site that constructs it.
3. The runner reads `P.image_text_evidence` as it does today and writes its
   resolved verdicts into the report's existing `image_text` array, which it
   already populates (`slide_capture_fit_runner.py:76`).
4. The capture copies each resolved verdict onto the element it belongs to, so
   `metrics.elements[qid].image_text_evidence.status` carries `no_text` or
   `font_floor_verified`, which is what `_validate_image_text_scope` reads.

Step 4 is the only new plumbing, and it is a copy, not a decision. The server
validator and the browser runner then agree by construction, because both are
looking at the same producer's record.

**Key by `qid`, not by asset identity.** The runner will also match on
`asset_digest`/`identity`, but that identity is hashed from `node.currentSrc`,
a value the browser resolves at load time and the server cannot reproduce.
`qid` is assigned server-side and is stable across the render. Asset identity
stays available as a fallback for a record that has no owning `qid`.

### 2.5 What is deliberately not changed

- The fail-closed default. An element with no record is refused exactly as now.
- The accepted status vocabulary, `no_text` and `font_floor_verified`.
- The runner's floor comparison, `minimum_text_pt >= floor_pt`.
- `IMAGE_TEXT_SCOPE_REVISION`, which participates in `acceptance_key`. Issuing
  records does not change the scope contract, so the revision holds. If a later
  change alters what a status means, that is when it moves.

## 3. Open questions for the owner of the image stage

1. Does the infographic asset pack's manifest already carry a text-free
   declaration per asset, or does it need one added? This design assumes the
   former and does not propose a new manifest field.
2. Does the illustration request path retain a stable request identity suitable
   as `review_digest` after a retry or a cache hit?
3. Source images: is there an existing measurement of embedded text size for an
   uploaded picture? If not, those slides keep failing closed, and that is the
   correct outcome until one exists.

## 4. Expected effect, stated honestly

Filling the gap does not repair slides by itself. It lets the fitter run on the
third of slides where it currently does not. On the five-slide probe that turned
into one repair, two clean slides and two that still exhaust their candidates.
The same rejection rules then apply, including `fixed_horizontal`, which the
non-verbatim baseline puts at 23.9% of judged candidates rather than the 42.7%
measured on a single document.

The scope-level reflow contract proposed separately remains justified and remains
second in line behind this.
