# Image-text evidence: one producer for a field that has two readers and none

- Date: 2026-09-11
- Status: **implemented 2026-09-12** — producer `services/authored_local_fit_image_text.py`,
  transport hunks in `utils/slide_capture_local_fit.py::capture_with_policy`, unit tests and a
  real-Chrome integration test. §5 records the measured effect on the existing corpus.
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

**Checkpoint resume constraint.** `services/authored_illustration_stage.py::illustrate_slide`
returns the slide's checkpointed HTML unchanged whenever
`checkpoint.illustrate_status in ("completed", "skipped")` (lines 97-103): on a
resumed job the stage body never runs again, so any evidence the original
design meant to carry out of that call as a second return value simply would
not exist on the resumed path. Evidence therefore cannot be a side channel
returned from the illustration stage; it has to be recoverable from the final
HTML plus server-held durable state alone. That constraint is why the design
below looks different from the one originally approved.

### 2.1 One producer at rest: the icon pack index

The illustration stage itself is unchanged; it places pictures exactly as it
did before this design existed. The durable record of provenance is not a
value returned from that call — it is the on-disk icon pack index
(`services/authored_icon_pack/store.py`, `index.json`). Every icon the pack
writes carries a `checks["text"]` entry on its `IconEntry`, written by
`services/authored_icon_pack/service.py:131-150` from
`services/authored_icon_pack/checks.py::check_no_text`'s result, recording
whether the check passed and by which `method` — `"ocr"` or `"heuristic"`.

The single function that turns that index into evidence is
`services/authored_local_fit_policy.py::image_text_evidence_for_html(html, *,
app_data=None)` (a thin, never-raising wrapper over
`services/authored_local_fit_image_text.py::image_text_evidence`). For each
`<img src="data:...">` in the slide's HTML it looks up the owning pack icon by
matching the data URI — via `icon_data_uri(pack.path_for(entry))`, not the
icon file's raw bytes, because `authored_icon_pack/inline.py::icon_data_uri`
may re-encode the PNG on the way to inlining — and issues a record only when
the matched entry's `checks.text.passed` is true and `checks.text.method ==
"ocr"`. A heuristic pass, a cutout, an illustration or an upload produces no
record, and therefore stays unverifiable.

### 2.2 Provenance decides the status, and nothing else looks at pixels

`image_text_evidence_for_html` distinguishes origins not through stage
parameters but through what durable state exists for the picture already in
the DOM. Each origin maps to one status:

| origin | how it's recognized | status | basis |
|---|---|---|---|
| Icon-pack icon | `<img>` whose `src` data URI matches a pack entry | `no_text`, only when the pack recorded an OCR-verified pass | `IconEntry.checks["text"]` with `passed: true`, `method: "ocr"` (written by `authored_icon_pack/service.py:131-150`). A `heuristic` pass — OCR unavailable, see `checks.py`'s module docstring — gets no record: fail-closed. |
| Scene cutout | `services/authored_icon_pack/scene.py::generate_scene_cutout` | **no record** | The cutout runs `check_no_text` (line 118) but is never written to the pack index — there is no `IconEntry` created for it and no `pack.append` call anywhere in `scene.py` — so there is no durable record to restate. Giving it evidence would require adding it to a pack index; this design does not add one. |
| Generated illustration | placed via `image_service` | **no record** | Not text-free by construction. `utils/authored_illustrations.py:682-683` states the slide's outline markdown is "the authoritative — and only — source of any label the image model is allowed to draw" and "it only reaches providers verified to render text" — i.e. a text-capable provider is allowed to render it. The parallel comment at `services/authored_illustration_stage.py:186-188` restates the same rule at the call site. Fail-closed here is correct, not a gap; this replaces the earlier design's claim of a no-lettering prompt. |
| User-uploaded source image | `source_images`, carried on `data-asset-id` | **no record** | No measurement of text in image pixels exists anywhere under `services/source_images/` (`extract.py`, `models.py`, `registry.py`, `selection.py`) or `services/authored_source_images.py`. The `source_text` fields present there are caption/matching text extracted from the source deck's own XML, not OCR of the picture. |
| Anything else | no provenance | **no record** | Fail closed, exactly as today. |

The table's point is narrower than the original design's. That design's rule
was "an image whose origin this system controls has a known text property,
and one whose origin it does not control keeps the current refusal." That
rule does not hold here: scene cutouts and generated illustrations are
origins this system fully controls, and both still get no record, because
neither has a durable, OCR-verified fact attached to it anywhere in server
state. The rule that actually holds is: a picture gets evidence only when a
specific, already-durable record says it passed OCR text verification. Today
that exists for exactly one origin, the icon pack. Everything else — including
origins the system fully controls — keeps the current refusal until an
equivalent durable record exists for it too.

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

One producer, computed fresh at measurement time from the final HTML, two
hops, no second source of truth.

1. `utils/slide_capture_local_fit.py::capture_with_policy` computes
   `image_text_evidence = policy.get("image_text_evidence") or
   image_text_evidence_for_html(html)` for the exact HTML being measured, and
   adds it to the policy passed into the browser render — next to the
   existing `installed_font_identity` augmentation, in the same dict-merge.
   This happens inside the existing capture path, so no production call site
   that constructs the policy needs to change, and the offline harness goes
   through the same function.
2. The browser fit runner reads `P.image_text_evidence` exactly as before
   (`utils/slide_capture_fit_runner.py::_imageTextCheck`) and writes its
   resolved per-element verdicts into the report's existing `image_text`
   array (`slide_capture_fit_runner.py:76`), unchanged.
3. Back in Python, `capture_with_policy` calls `merge_image_text_verdicts(metrics,
   report)`, defined in the same module next to `capture_with_policy`. It is a
   pure function: it copies only the records `report["image_text"]` marked
   `verified: true` onto the element with the matching `qid`, leaves an
   element that already carries `image_text_evidence` untouched, drops a
   record whose qid matches no element, and changes no other element field —
   it never decides anything, it only copies the runner's own verdict.
4. `services/authored_local_fit_evidence.py::_validate_image_text_scope` reads
   `metrics.elements[qid].image_text_evidence.status`, `no_text` or
   `font_floor_verified`, exactly as before.

Steps 1 and 3 are the new plumbing, both inside
`utils/slide_capture_local_fit.py`. This differs from the originally approved
flow, where the illustration stage itself was meant to be the producer and the
pipeline was meant to thread its return value onto the policy; the checkpoint
constraint in §2 ruled that out, so the producer moved downstream to
measurement time instead, reading durable state rather than a returned value.

**Matched by `qid`; asset digest does not actually reconcile across the
boundary.** Both sides compute an identity-like field, but not the same way:
Python's `asset_digest` hashes the literal `src` attribute string
(`src_digest`, `authored_local_fit_image_text.py`), while the browser's
`identity` hashes the node's tag name, `node.currentSrc` (falling back to
other src-like attributes) and its computed `backgroundImage`
(`slide_capture_fit_runner.py:26-27`) — values the browser resolves at load
time that the server cannot reproduce. Matching therefore happens by `qid`,
which the server assigns and which
`image_text_evidence`'s own HTML parser resolves the same way the browser's
`closest('[data-qid]')` does.

### 2.5 What is deliberately not changed

- The fail-closed default. An element with no record is refused exactly as now.
- The accepted status vocabulary, `no_text` and `font_floor_verified`.
- The runner's floor comparison, `minimum_text_pt >= floor_pt`.
- `IMAGE_TEXT_SCOPE_REVISION`, which participates in `acceptance_key`. Issuing
  records does not change the scope contract, so the revision holds. If a later
  change alters what a status means, that is when it moves.

## 3. Answers

The original design left three questions for the owner of the image stage.
The implemented design answers them directly rather than assuming:

1. **Yes.** The icon pack index carries a per-icon text check with an explicit
   method. `IconEntry.checks["text"]` is written by
   `services/authored_icon_pack/service.py:131-150` as `text.to_dict()`
   (`services/authored_icon_pack/checks.py:106-121`), which always includes
   `passed` and `method` (`"ocr"` or `"heuristic"`,
   `checks.py:103`). No new manifest field was needed.
2. **Moot for illustrations; answered for icons.** Illustration request
   identity never had to be resolved, because generated illustrations stay
   fail-closed regardless of retry or cache behavior (§2.2) — they are not
   text-free by construction, so no `review_digest` is ever issued for one.
   For icons, the identity used as `review_digest` is the pack file itself
   (`_review_digest`, `services/authored_local_fit_image_text.py:74-79`: a
   digest of the pack key, the entry's `file` name and the file's own bytes).
   That identity is stable across a retry or a cache hit by construction:
   `IconPack.write_icon` names a file deterministically from the concept key
   (`store.py:151-156`, `icon_file_name(key)`), and `IconPack.append` keeps
   the first writer's file when two callers race on the same concept,
   discarding the later duplicate (`store.py:166-191`, specifically the
   dedup check at 176-180).
3. **No.** There is no existing measurement of embedded text size or
   presence for an uploaded source image anywhere under
   `services/source_images/` or `services/authored_source_images.py`. Those
   slides keep failing closed, which remains the correct outcome until such a
   measurement exists.

## 4. Expected effect, stated honestly

Filling the gap does not repair slides by itself. It lets the fitter run on the
third of slides where it currently does not. On the five-slide probe that turned
into one repair, two clean slides and two that still exhaust their candidates.
The same rejection rules then apply, including `fixed_horizontal`, which the
non-verbatim baseline puts at 23.9% of judged candidates rather than the 42.7%
measured on a single document.

This also narrows what the probe predicts for the fix actually built. The
producer that now exists covers exactly one origin — pack icons — not every
raster on a slide (§2.2). Of the 414 slides flagged `font_unverifiable` with
zero candidates, only those whose raster element is a pack icon, rather than
a scene cutout, a generated illustration or an uploaded picture, are unblocked
by this change. The measured effect will therefore be a subset of 414, and the
remaining slides staying unresolved is expected and correct, not a regression.

The scope-level reflow contract proposed separately remains justified and remains
second in line behind this.

## 5. Measured effect on the existing corpus (2026-09-12)

The 414 zero-candidate slides from the non-verbatim baseline were re-rendered
through the implemented path. **0 of 414 changed**; the runner issued 721
image-text records, all `unverifiable`. Two facts explain it, and both are
recorded in
[`artifacts/local-fit-source-reflow-replay-20260911/report.md`](../artifacts/local-fit-source-reflow-replay-20260911/report.md):
this `app_data` holds no icon pack, and no stored slide carries an icon-pack
placement, so the only provenance the producer can restate never occurs; and
the 401 image slides are generated illustrations, which are not text-free by
design and correctly keep failing closed.

The mechanism is verified in real Chrome by
`tests/integration/test_local_fit_image_text_evidence_chrome.py` (verified icon
fits; heuristic-only and unrecorded icons fail closed). The corpus number will
move only when a producer exists for generated illustrations' text size, or when
decks are produced with pack icons.
