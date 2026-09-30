# SVG style-brief canvas rewrite and the reference-image experiment (budget S): plan (2026-09-29)

Status: design only. No code was changed. Every file:line was read from the worktree on branch
`serve/main-20260902` on 2026-09-29; paths are under `servers/fastapi/` unless stated. Numbers marked
"measured" come from the 22 receipts under `artifacts/canvas-bench-20260929/pooled-candidate/`; numbers
marked "estimate" were not measured.

Supersedes the sample plan and switch mechanics of
`docs/svg-style-reference-canvas-experiment-design-20260929.md` (kept for the metric definitions, the board
spec and the threat list; sections 2, 5 and 8 of that document are replaced here). Related:
`docs/aspect-ratio-expansion-design-20260928.md` (the HTML author's canvas-token rewrite),
`docs/canvas-bench-decision-rule-design-20260929.md`, `docs/svg-master-no-stretch-and-variant-removal-design-20260929.md`
(being revised by another session; not referenced for facts here).

Two approved items:

- **Part 1** — stage-1 item (1): rewrite the 16:9-worded style-brief lines the svg author receives so that
  an off-16:9 deck is not told to compose "at 1280x720" or "on a 16:9 grid", with goldens pinning the 16:9
  prompt bytes committed first.
- **Part 2** — the reference-image experiment at budget S with outlines fixed per (manuscript, canvas),
  run after Part 1 has landed so the text anchor no longer confounds the image arms.

---

## Part 1. SVG style-brief canvas rewrite

**Implementation (2026-09-29): landed on `serve/main-20260902` as two commits, in the order §1.6 requires.**

- S1 goldens (commit `e76261cd5`): `svg_design_text()` extracted in `utils/llm_calls/author_slide_svg.py`;
  `scripts/capture_svg_design_baseline.py`; 39 fixtures under `tests/fixtures/svg_design_16x9_baseline/`;
  `tests/unit/test_svg_design_16x9_baseline.py` (45 ids byte-identical incl. alias → canonical and the
  `exact-osb-last` raise, 39 digest checks, fixture-set completeness). Passed on unchanged code.
- S2 rewrite + caveat (the commit after it): `services/generation_core/drivers/svg/canvas_brief.py`
  (`svg_brief_replacements` R1–R9, `adapt_brief_line`, `reference_caveat`, `SVG_CANVAS_BRIEF_VERSION`);
  the hook in `project_style.py` plus `provenance.canvas_brief_rewrite` off-16:9; public `shape_word()` in
  `author_canvas_shape.py` (`BASELINE_TOKENS` untouched); `svg_author_rules` appends `reference_caveat`
  after `composition_clause`; `tests/unit/test_svg_canvas_brief.py`. S1 goldens passed unchanged, which is
  the 16:9 byte/`style_digest` proof.
- Decisions taken as recommended: D1 unconditional, D2 px sizes unscaled, D3 `overrides` untouched, D4
  caveat on shape only. `reference_caveat` lives in `canvas_brief.py` (imported by `author_slide_svg.py`)
  rather than inline in `svg_author_rules`, so the three svg canvas-wording rules share one module.
- Measured correction to §1.2: the anchored `common_guidance` occurrences number **39**, not 44
  (A 14 + B 21 + C 3 + D 1; broadside, devax-si-proposal and signal carry two each, so 36 styles). `1280 by
  720` occurs in no `common_guidance` line (only in screenshot-blue's override), so R9 is a pure safety net.
  After the rewrite every registry canvas (1188x840, 840x1188, 720x720, 1680x720, 720x1280, 720x1040)
  carries 0 anchors in `common_guidance`; both override anchors survive byte for byte.
- Correction to the §1.3 table's 840x1188 column: `aspect_ratio_label(840, 1188)` is `840x1188` (the pair
  reduces to 70:99, past the 32 cap), not `9:16`, so R4 yields "Compose for a fixed 840x1188 portrait
  canvas"; the `9:16 portrait` wording belongs to 720x1280. The code follows the function as §1.3 defines
  it; the test pins both.
- Not implemented from §1.7 item 7: the `design_pack_template` pass-through check, because the design-pack
  variant path was being removed by another session in the same worktree at the time; `mapped_guidance`
  and `master_template` (stub master) are covered.
- S3 (shared-backend restart and the two live decks of §1.9) was not part of this change.

### 1.1 Facts (re-verified)

Path of a brief line into the svg prompt:

1. `utils/svg_style_adapter.py:81-89` `adapt_svg_style` builds a `StyleContext` from the resolved style;
   `:53-54` rejects `exact_reconstruction` styles; `:55-58` admits only the three `svg_overrides` keys.
2. `services/generation_core/style/build.py:82-98` `tag_brief_lines` splits `style.brief` into lines and
   tags each `{"svg","html"}` or `{"html"}`; `:30-33` `_HTML_ONLY_TERMS` (`HTML|CSS|DOM|flexbox|flex|
   grid-template|position:absolute|exemplar` or an inline tag) is what marks a line html-only. No canvas
   is known here (the function is format-neutral and takes no size).
3. `services/generation_core/drivers/svg/project_style.py:54-57` collects the svg-tagged lines into
   `common_guidance[section]`; `:58-75` builds the flat dict with `canvas`, `tokens`, `common_guidance`,
   `overrides` (the YAML `svg_overrides`, passed through unchanged) and `provenance.brief_digest`
   (`:72`, a hash of the untagged YAML lines).
4. `svg_style_adapter.py:107-109` hashes the whole dict (including `common_guidance`, `overrides` and the
   reference image bytes) into `digest`.
5. `utils/llm_calls/author_slide_svg.py:112-113` dumps every snapshot key except `references` and
   `diagnostics` as the `design` string; `:120-123` prepends it to the per-slide JSON; `:135-136` passes it
   to `author_messages` (`utils/llm_calls/author_prompt_split.py:168-198`). `:202` writes
   `"style_digest": style_snapshot["digest"]` into every authoring receipt.
6. `svg_author_rules` (`author_slide_svg.py:75-79`) appends `composition_clause(width, height)` from
   `utils/llm_calls/author_canvas_shape.py:73-105`, which is the empty string at 1280x720 (`:75-76`).
   `baseline_replacements` (`:43-61`) is used only by the HTML author (`apply_canvas_tokens`, `:67-70`);
   nothing rewrites svg `common_guidance`. `BASELINE_TOKENS` (`:64`) is pinned by
   `tests/unit/test_author_canvas_shape.py:148-162` (`EXPECTED_TOKENS` must equal the token set found in
   the frozen HTML profiles), so adding a token there fails that test and would also touch the frozen
   authored prompts: the svg rewrite needs its own replacement list.
7. Reference images are appended after the text with no size note (`author_slide_svg.py:124-132`); the
   only sentence about them is rule line `:49` ("References show art direction only").
8. Frozen snapshot: `services/svg_generation_admission.py:151` `pin_svg_style` returns
   `request._svg_style_snapshot` when set and builds it once otherwise (`:152-176`); the durable payload
   stores it as `_internal_svg_style_snapshot`
   (`api/v1/ppt/endpoints/presentation_generation_staging.py:118`) and the worker restores it (`:322`,
   `:350`); `services/svg_presentation_prepare.py:110-116` refuses a job whose stored
   `svg_style_snapshot` differs from the restored one (`svg_style_snapshot_conflict`).
9. Goldens: the only svg prompt pin at 16:9 is `svg_rules.txt` via
   `tests/unit/test_author_canvas_shape.py:173-176`. `tests/unit/test_svg_style_adapter.py` (8 tests)
   asserts token precedence, never the `design` bytes. No test pins `common_guidance` or `digest`.

Two facts the earlier review did not state:

- **Aliases.** `utils/authored_styles.py:642` canonicalises the requested id before lookup; five YAML ids
  resolve to another style's YAML and their own brief text is never read by svg:
  `atelier-monograph` and `soft-editorial` → `minimal-vellum`; `decision-atlas` and `strategic-insight` →
  `strategic-navy`; `product-lab` → `silicon-refined` (probed: identical `design` strings, resolved
  `style.id`). `exact-osb-last` is rejected at resolution (`AuthoredExactReconstructionUnavailableError`)
  and never reaches `adapt_svg_style`. So the svg-admitted catalogue is **39 canonical styles**, reachable
  by 44 ids.
- **`svg_overrides.common_guidance` reaches the prompt through `overrides`, not `common_guidance`**
  (`project_style.py:66`), and is not run through `tag_brief_lines`. `screenshot-blue.yaml:44` and
  `chroma-fold.yaml:34` live there.

### 1.2 Anchor inventory (45 YAML files, 53 matching lines)

Regex over `authored_styles/*.yaml`: `1280x720 | 1280 by 720 | 16:9 | wide screens | aspect ratio`. The
classification below is by what reaches the svg prompt after `_HTML_ONLY_TERMS` and alias resolution.

**Class A — rewrite, `brief` line "At 1280x720, use the Korean-safe stack ... Body text should normally be
22-28px ..." (14 canonical styles):** academic-edge:43, clinical-precision:43, default:36,
devax-si-proposal:69, exec-report:45, flat-infographic:53, hyundai-culture-center:68, liquid-executive:43,
prime-noir:43, scholars-journal:43, science-sketch:42, sketch-note:56, strategic-navy:36 (also serves
decision-atlas, strategic-insight), structured-mint:43.

**Class B — rewrite, `brief` line "... sans-serif. At a 1280x720 canvas, target body 22-28px and labels at
least 16px ..." (21 canonical styles):** architectural-portfolio:51, botanical-journal:51, broadside:51,
cobalt-editorial:51, cyber-ai:66, editorial-tritone:45, geometric-mono:51, groovy-70s:51, iso-blueprint:65,
luxury-editorial:51, minimal-vellum:48 (also serves atelier-monograph, soft-editorial), neo-grid-bold:47,
neon-venture:49, prestige-gold:51, prismatic-tech:44, project-launch:44, signal:74, silicon-refined:52
(also serves product-lab), startup-aura:44, tech-blueprint:72, visual-discovery:44.

**Class C — rewrite, composition anchors in `brief` (3 lines, 3 styles):**
- broadside:54 "Compose for a fixed 16:9 canvas with generous outer margins ..."
- devax-si-proposal:72 "Work on a disciplined 16:9 grid with 48–64px safe margins ..."
- osb-smart-banking:88 "Use a wide 16:9 corporate grid with outer margins sized to the content density ..."

**Class D — rewrite, scale anchor in `brief` (1 line):** signal:65 "Scale on the 1280x720 canvas: display
120px w700 ...".

**Class E — keep (not a canvas anchor, or already canvas-aware):**
- screenshot-blue:44 (`svg_overrides`) "The pinned canvas width and height are authoritative. A4 landscape
  is 1188 by 840 ... never output the 1280 by 720 preview size when the request pins A4." Must survive
  byte for byte.
- chroma-fold:34 (`svg_overrides`) "Scale text to the pinned canvas, using 1280x720 as the design
  reference: covers ... 76-100px ..." Already instructs scaling to the pinned canvas; the 1280x720 mention
  is the reference for the listed px sizes. Kept because `overrides` are the style author's explicit
  svg text and rewriting the number would mislabel the px table (open decision D3).
- screenshot-blue:40 (`preserveAspectRatio=xMidYMid meet`), osb-smart-banking:100 ("without distorting
  their aspect ratio" — supplied screens), screenshot-blue:80 ("two or three columns for wide screens" —
  screenshots, not the canvas).
- The rest of devax-si-proposal:72 and osb-smart-banking:88 (left-to-right process, 60/40, support
  column) is layout vocabulary, not a size anchor; `composition_clause` is declared authoritative and
  governs it.

**Class F — html-filtered anyway (7 lines; contain `CSS` or `HTML`, dropped at `build.py:96`):**
atelier-monograph:31, chroma-fold:47, decision-atlas:31, osb-smart-banking:85, product-lab:32 ("1280 by
720 CSS canvas"), screenshot-blue:56 ("At 1280x720, start with 34-42px titles" — in an `HTML` line),
screenshot-blue:59 ("use 1280x720 as the landscape design reference" — `HTML/CSS`).

**Class G — dead for svg:** exact-osb-last:24 and :209 (style rejected at resolution); the five alias
YAMLs' own briefs.

**No anchor at all:** kbs-company-proposal (its references are 750x1083 portrait).

Counts that matter: of the 39 canonical styles, **36 carry an anchor in `common_guidance`** (A+B+C+D:
44 line occurrences, since broadside, devax-si-proposal and signal carry two each), **2 carry one only in
`overrides`** (chroma-fold, screenshot-blue; kept), **1 carries none** (kbs-company-proposal). The
"39 of 45" figure from the review counted YAML files; by prompt reach it is 36 rewrite + 2 keep of 39.

### 1.3 Replacement table (svg-only, off-baseline only)

Inputs: `width, height`; `shape = classify_canvas_shape(w, h)` (`author_canvas_shape.py:24-34`);
`label = aspect_ratio_label(w, h)` (`:37-40`: `1:1`, `7:3`, `9:16`, `9:13`, or `1188x840` when a term
exceeds 32); `sw = shape word` from `_SHAPE_WORDS` (`:21`: `portrait `, `square `, `ultrawide `; empty for
`wide-landscape`). At `shape == BASELINE` the table is empty and no line is touched.

Applied per `common_guidance` line, in this order, with plain `str.replace` (specific phrases first, then
generic tokens as a safety net so a future YAML edit cannot reintroduce an anchor unnoticed):

| # | phrase in YAML | class | replacement | 720x720 | 1680x720 | 1188x840 | 840x1188 |
|---|---|---|---|---|---|---|---|
| R1 | `At 1280x720, ` | A | `At {W}x{H}, ` | At 720x720, | At 1680x720, | At 1188x840, | At 840x1188, |
| R2 | `At a 1280x720 canvas, ` | B | `At a {W}x{H} {sw}canvas, ` | At a 720x720 square canvas, | At a 1680x720 ultrawide canvas, | At a 1188x840 canvas, | At a 840x1188 portrait canvas, |
| R3 | `Scale on the 1280x720 canvas:` | D | `Scale on the {W}x{H} canvas:` | ... 720x720 canvas: | ... 1680x720 canvas: | ... 1188x840 canvas: | ... 840x1188 canvas: |
| R4 | `Compose for a fixed 16:9 canvas` | C | `Compose for a fixed {label} {sw}canvas` | ... fixed 1:1 square canvas | ... fixed 7:3 ultrawide canvas | ... fixed 1188x840 canvas | ... fixed 9:16 portrait canvas |
| R5 | `Work on a disciplined 16:9 grid` | C | `Work on a disciplined {label} {sw}grid` | ... 1:1 square grid | ... 7:3 ultrawide grid | ... 1188x840 grid | ... 9:16 portrait grid |
| R6 | `Use a wide 16:9 corporate grid` | C | `Use a {label} {sw}corporate grid` | Use a 1:1 square corporate grid | Use a 7:3 ultrawide corporate grid | Use a 1188x840 corporate grid | Use a 9:16 portrait corporate grid |
| R7 | `1280x720` (residual) | net | `{W}x{H}` | | | | |
| R8 | `16:9` (residual) | net | `{label}` | | | | |
| R9 | `1280 by 720` (residual) | net | `{W} by {H}` | | | | |

Rules of the table:

- Pixel type sizes that follow R1/R2/R3 (22-28px body, 120px display) are **not** rescaled. This matches
  the HTML path (`baseline_replacements` substitutes the canvas name and leaves px values alone) and the
  clause in `composition_clause` for wide-landscape ("keep the same margins rather than setting smaller
  type"); the body-font floor (`generation_font_floor`) is the only size authority. Decision D2.
- Whitespace: `{sw}` carries its own trailing space, so wide-landscape never produces a double space
  (tested).
- The table is applied only to `common_guidance` lines. `overrides`, `mapped_guidance`,
  `master_template` and `design_pack_template` are passed through untouched, which is what keeps
  screenshot-blue:44 intact without a special case.
- `provenance.brief_digest` keeps hashing the YAML lines before rewriting (it identifies the brief source,
  not the projection). The rewritten text is covered by `digest`.

### 1.4 Insertion point and function shape

New module `services/generation_core/drivers/svg/canvas_brief.py` (about 60 lines):

```python
SVG_CANVAS_BRIEF_VERSION = "svg-canvas-brief/v1"

def svg_brief_replacements(width: int, height: int) -> tuple[tuple[str, str], ...]:
    """R1..R9 for this canvas; () at the 1280x720 baseline."""

def adapt_brief_line(text: str, width: int, height: int) -> str:
    for old, new in svg_brief_replacements(width, height):
        text = text.replace(old, new)
    return text
```

It imports `classify_canvas_shape`, `aspect_ratio_label` and a new public `shape_word(width, height)`
from `utils/llm_calls/author_canvas_shape.py` (a 4-line accessor over `_SHAPE_WORDS`; adding it changes
no prompt bytes and no existing test). `BASELINE_TOKENS` and `baseline_replacements` are not touched.

Hook in `project_style.py:54-57`:

```python
common_guidance: dict[str, list[str]] = {}
for line in context.brief_lines:
    if "svg" in line.applies_to:
        common_guidance.setdefault(line.section, []).append(
            adapt_brief_line(line.text, width, height))
```

and, only when `svg_brief_replacements(width, height)` is non-empty,
`result["provenance"]["canvas_brief_rewrite"] = SVG_CANVAS_BRIEF_VERSION`. The key is absent at 16:9 so the
16:9 dict stays byte-identical; off-16:9 it lets a receipt reader tell a pre-rewrite snapshot from a
post-rewrite one without diffing text.

Why here and not in `tag_brief_lines`: `build.py` is format-neutral and has no canvas; `project_style` is
the only svg-specific step that knows `width, height` and is the single point where every svg brief line
passes (source-restyle `template_snapshot` at `svg_style_adapter.py:110-112` reads the already-rewritten
`result`). No per-style special case exists: screenshot-blue survives because `overrides` are out of scope,
not because of a name check.

### 1.5 Reference-image caveat

Appended in `svg_author_rules` (`author_slide_svg.py:75-79`) after `composition_clause`, only when
`classify_canvas_shape(width, height) != BASELINE`:

```
- REFERENCE IMAGES: the attached style references are art-direction samples drawn on other page shapes
  (mostly 16:9). Take palette, type, component treatment and imagery tone from them; never their
  proportions, column count or frame. Fill the {W}x{H} canvas; do not letterbox it to match a reference.
```

Conditioned on shape only, not on whether references exist, so all three experiment arms carry identical
rules text (decision D4). At 16:9 `svg_author_rules` is unchanged and `svg_rules.txt` keeps passing. The
mention of "16:9" in this sentence lives in the rules, not in `common_guidance`, so the "no anchor
survives" test (1.7) is scoped to `common_guidance` and does not trip on it.

`tests/unit/test_author_canvas_shape.py:236-241` (`test_svg_rules_carry_the_shape_clause`) asserts
`rules.endswith(composition_clause(w, h))`; it changes to `endswith(composition_clause(w, h) +
reference_caveat(w, h))`. That test covers off-16:9 only; the 16:9 golden (`:173-176`) is untouched.

### 1.6 Goldens (separate commit, before the rewrite)

**What is pinned.** For each of the 39 canonical styles at 1280x720, the exact `design` string as
`author_slide_svg` builds it (`json.dumps({k: v for k, v in snapshot.items() if k not in {"references",
"diagnostics"}}, ensure_ascii=False)`), with no request overrides (`adapt_svg_style(style, width=1280,
height=720)`), no master, no design pack. It contains `canvas`, `tokens`, `common_guidance`, `overrides`,
`mapped_guidance`, `provenance` and `digest` — so it pins the receipt-visible `style_digest` too, and
because `digest` covers the reference-image bytes, a re-encoded asset also shows up. Sizes probed: 4.9 KB
to 11.0 KB per style, about 290 KB in total.

**Files.** `tests/fixtures/svg_design_16x9_baseline/design_<canonical-id>.txt` (39 files, UTF-8, LF;
read as bytes and CRLF-normalised like `test_author_canvas_shape.py:97-99`).

**Identity refactor in the same commit.** Extract `svg_design_text(style_snapshot) -> str` in
`author_slide_svg.py` (the two lines at `:112-113`) and call it from `author_slide_svg`; the golden test
calls the same function so the pinned string cannot drift from the prompt builder. Behaviour-identical.

**Capture.** `scripts/capture_svg_design_baseline.py` (about 50 lines): for every `authored_styles/*.yaml`
id, resolve; skip ids whose resolved `style.id` differs (aliases) and ids that raise
`AuthoredExactReconstructionUnavailableError`; write `design_<style.id>.txt`. Run once with
`APP_DATA_DIRECTORY`/`USER_CONFIG_PATH` set and no `AUTHORED_*`/`SVG_*` env (the test clears them, as
`test_author_canvas_shape.py:67-74` does). Recapture is a deliberate act: rerun, review the diff, commit
with the reason (contract version bump, font bundle change, YAML edit).

**Test** `tests/unit/test_svg_design_16x9_baseline.py`:

- `test_16x9_design_is_byte_identical[<id>]` for all 45 YAML ids: alias ids compare against their canonical
  fixture (`resolve_authored_style(id).id`), `exact-osb-last` is asserted to raise.
- `test_every_baseline_fixture_is_exercised`: the fixture directory equals the canonical id set (catches a
  removed or renamed style).
- `test_16x9_digest_matches_fixture`: `snapshot["digest"]` equals the `digest` inside the fixture string
  (redundant with byte identity, but names the receipt field in the failure message).

Committed **before** 1.4/1.5. The rewrite commit must leave this test green with no fixture change: that
is the proof the 16:9 bytes and `style_digest` did not move.

### 1.7 Unit tests for the rewrite (`tests/unit/test_svg_canvas_brief.py`)

Sizes: `(1188, 840), (840, 1188), (720, 720), (1680, 720), (720, 1280), (720, 1040)`.

1. For every admitted YAML id × size: the joined `common_guidance` matches none of
   `1280x720|16:9|1280 by 720`, and contains `f"{w}x{h}"` for the 36 anchored styles (and does not for
   kbs-company-proposal; a table of expected counts per style keeps the test honest).
2. `svg_brief_replacements(1280, 720) == ()`; `adapt_brief_line` is the identity at 16:9 for every line of
   every style.
3. For every id × size: `snapshot["overrides"]` equals the 16:9 `overrides` byte for byte; explicitly,
   screenshot-blue's override still contains "never output the 1280 by 720 preview size" and
   chroma-fold's still contains "using 1280x720 as the design reference".
4. R4/R5/R6 produce the strings in the table for 720x720, 1680x720, 1188x840, 840x1188; no `"  "` in any
   rewritten line.
5. `provenance.brief_digest` is identical across sizes; `digest` differs from 16:9 at every other size and
   is stable across two calls; `provenance.canvas_brief_rewrite` is present off-16:9 and absent at 16:9.
6. `svg_author_rules` off-16:9 ends with `composition_clause + reference_caveat`; at 16:9 it equals
   `svg_rules.txt` (existing test).
7. `mapped_guidance`, `master_template` (with a stub master) and `design_pack_template` are unchanged
   across sizes.

Runtime: 44 ids × 6 sizes × one image read per reference (about 300 KB each) is a few seconds.

### 1.8 Digest and receipt impact

- 16:9: `svg_brief_replacements` returns `()`, `common_guidance` is byte-identical, `provenance` gains no
  key, so `digest` and every receipt's `style_digest` (`author_slide_svg.py:202`) are unchanged. The
  golden test is the gate.
- Off-16:9: `digest` changes for the 36 anchored styles (and for all 39 through the new `provenance`
  key). Consumers: `style_digest` in authoring receipts and quality receipts, `svg_style_snapshot` on the
  job row (`models/sql/presentation_generation_job.py:83`), the bench receipt. None compares a digest
  across deployments; the conflict check at `svg_presentation_prepare.py:110-116` compares the job's own
  stored snapshot with the restored one, and both come from the same frozen payload
  (`presentation_generation_staging.py:118`, `:322`, `:350`), so an in-flight off-16:9 job resumed after
  the deploy keeps its pre-rewrite text and digest and does not conflict. Only jobs admitted after the
  restart get the new text.
- Bench: `metricEpoch` stays `svg-overlap-ink-v1`; the rewrite is a prompt change, so bench receipts must
  carry the new `backendCommit` and the experiment refuses to pool pre-rewrite decks (existing rule).

### 1.9 Rollout

Unconditional (no env knob), like `apply_canvas_tokens` on the HTML path (decision D1). Backend restart on
the shared stack via `scripts/start-local-stack.ps1` (no reloader); no migration. Verification after
restart: one A4-landscape svg deck; read `presentation_generation_jobs.svg_style_snapshot` for that job
and confirm `common_guidance` carries `1188x840` and `provenance.canvas_brief_rewrite`; one 16:9 deck and
confirm its `style_digest` equals the pre-restart value for the same style (the fixture's `digest`).

### 1.10 Risks

- **Unscaled px after R1/R2.** On a 720-wide canvas "body 22-28px" is proportionally larger than at 1280.
  Same behaviour as the HTML path today; the floor and `composition_clause` already govern. If the bench
  shows oversized type on 1:1, the fix is a size-scaling rule in `canvas_brief.py`, not per-style text.
- **Residual layout vocabulary** in class C lines (60/40, left-to-right, three-card groups elsewhere)
  still reads 16:9-ish; `composition_clause` is authoritative and tested to be present. Not a rewrite
  target.
- **chroma-fold/screenshot-blue overrides** keep their anchors by design; neither is in the S experiment.
- **Golden coupling to asset bytes**: any reference PNG re-encode fails 39 fixtures at once. Intended
  (`style_digest` really does change then), but recaptures must be reviewed, not rubber-stamped.
- **Shared worktree**: `tests/unit/test_author_canvas_shape.py` is touched (2 lines); another session's
  uncommitted edits are in `scripts/canvas_bench_live.py` and `tests/unit/test_canvas_bench.py` only, so no
  overlap with Part 1.

---

## Part 2. Reference-image experiment, budget S, fixed outlines

### 2.1 Design (user decision: S, fixed outlines)

- **Cells**: canvases `square-1x1`, `ultrawide-21x9`, `a4-landscape` × styles `default`, `strategic-navy`
  × manuscripts `tests/fixtures/canvas_bench_manuscripts/01..04` = **24 cells**.
- **Arms**: A current references (1280x720 `content` image), B no references, C style board. **72 decks**.
- **Outline fixed per (manuscript, canvas)**: one outline per pair (12), sent as `slides_markdown` to all
  6 decks of the pair (2 styles × 3 arms). Structural basis: the validator
  `models/generate_presentation_request.py:306-313` sets `n_slides = len(slides_markdown)`;
  `api/v1/ppt/endpoints/presentation_generate.py:910-912` marks `using_slides_markdown`, `:1108-1115`
  builds `PresentationOutlineModel` from the strings and skips the outline stage; the svg driver receives
  that outline (`services/svg_presentation_service.py:46`) and pages are `[item.content for item in
  outline.slides]` (`svg_presentation_prepare.py:139`).
- **Roles**: assignment is index-based, not parsed from content —
  `svg_presentation_prepare.py:154` `roles = ["cover" if i == 0 and request.include_title_slide else
  "content" ...]`; `include_title_slide` defaults to `True` (`generate_presentation_request.py:255`) and
  `slides_markdown` does not alter it (only source-restyle does, `:364`). There is no `closing` role on
  the svg path at all. So from markdown, slide 0 is `cover` and the rest `content`, exactly as in a product
  run. What remains **unverified** is whether the pinned slide-0 text yields a cover-shaped slide and whether
  any svg stage still re-plans content (asset offers at `:165` read the outline; content budgeting is not
  visible in the prepare path). The smoke run answers this (2.6).
- Run after Part 1 lands; every deck must carry a `backendCommit` that includes the rewrite.

### 2.2 Arm switch (bench-only, rides the frozen snapshot)

Why it must live in the snapshot: the request field is `exclude=True`, and the durable payload is
`request.model_dump(mode="json")` (`presentation_generation_staging.py:92`), which drops excluded fields;
the worker rehydrates from that payload (`:322`, `:350`) and would never see the arm. But `pin_svg_style`
runs inside `_durable_request_payload` in the API process (`:118`) on the in-memory request, and its result
is persisted as `_internal_svg_style_snapshot`; the worker's `pin_svg_style` returns it unchanged
(`svg_generation_admission.py:151`). Anything written into the snapshot at admission therefore survives
takeover and restart.

1. `models/generate_presentation_request.py`: `bench_reference_condition: Optional[Literal["current",
   "none", "board"]] = Field(default=None, exclude=True)`.
2. `svg_generation_admission.py` `pin_svg_style`, before `adapt_svg_style`: if the field is non-null and
   env `CANVAS_BENCH_ENABLED != "true"` → `HTTPException(422, "bench_field_disabled")`; else pass
   `reference_condition=` through to `adapt_svg_style`.
3. `utils/svg_style_adapter.py`: new keyword `reference_condition: str | None = None`; after the
   `references` dict is built (`:60-80`) call
   `references, meta = apply_reference_condition(references, reference_condition, style_id, board_dir)`
   from new `services/generation_core/drivers/svg/reference_condition.py` (`None`/`current` → unchanged,
   `meta=None`; `none` → `{}`; `board` → `{"content": [board]}` from `$SVG_STYLE_BOARD_DIR/<style_id>/
   board.png`, PNG ≤ 1536 KiB, missing board → `ValueError("svg_reference_board_missing:<style>")`). When
   `meta` is set, `result["reference_condition"] = meta` before the digest (`:107`), so `digest` differs
   per arm and the arm is recorded in the frozen snapshot.
4. `author_slide_svg.py`: add `reference_condition` to the exclusion set at `:112` (prompt text identical
   across arms apart from the 64-hex `digest` value inside `provenance`-adjacent `digest`, see T4b) and
   copy `style_snapshot.get("reference_condition")` into the receipt at `:201-210` (absent on the default
   path, so existing receipt fixtures do not change).
5. Bench: the runner records the requested arm; after the deck completes it reads the arm from the
   authoring receipts (the quality receipts the bench already collects, `canvas_bench_live.py:179`
   `quality_receipts_between`) and fails the deck (`invalid: arm_mismatch`) if any slide's
   `reference_condition` disagrees or if arm B shows a style image (cover slide `input_image_count` must
   be 0 in B; content slides may carry offered icons, so the check is on the snapshot field, not the
   count).

Tests: default path unchanged (the Part 1 golden proves the 16:9 bytes; add one off-16:9 case that
`reference_condition=None` equals the call without the argument), `none` empties references and sets
`meta`, `board` loads and validates the PNG, missing board raises, disabled env → 422 through the endpoint
model. Removal after the experiment: the module, the two hooks, the field and the gate.

### 2.3 Style boards for the two pilot styles

Spec as in the base document §3 (1280x1280 PNG, nine equal tiles, no slide chrome, no size words),
produced deterministically:

- `scripts/style_board_render.py` (new, about 250 lines): input `authored_styles/<id>.yaml` render keys
  (palette, fonts, preview) + `authored_style_boards/<id>/spec.json` (closed-vocabulary treatment spec,
  schema `scripts/style_board_spec.schema.json`); output an SVG built from one curated component sheet,
  rasterised through the existing Chromium svg renderer (`services/svg_renderer.py`) at 1280x1280; fails
  on tile overflow or > 1536 KiB.
- `scripts/check_style_boards.py`: manifest `authored_style_boards/manifest.json` (`authored-style-boards/
  v1`: `style_id`, `board_version`, `source_style_digest` over the render keys, `spec_digest`,
  `renderer_commit`, `png_sha256`, `bytes`, `reviewed_by`, `reviewed_at`); stale when the YAML render keys
  changed.
- Review: the user reviews `default` and `strategic-navy` boards against the checklist (palette matches
  preview; the style's own fonts with Korean glyphs; no slide-like arrangement; nothing copied from the
  1280x720 reference; recognisable next to the current reference). A failed board is fixed in spec or
  renderer, never by editing the PNG.
- Directory is outside packaged assets and read only when `SVG_STYLE_BOARD_DIR` is set.

### 2.4 Runner changes (`scripts/canvas_bench*.py`)

Note: `canvas_bench_live.py` and `tests/unit/test_canvas_bench.py` currently carry another session's
uncommitted edits (`_settle`, 84 lines); the changes below are additive elsewhere and rebase on top.

- `canvas_bench_matrix.py`: `Deck` gains `style: str`, `arm: str`, `outline: Path | None`; `stem` becomes
  `<manuscript>-<style>-<arm>`; new `plan_experiment(canvases, styles, manuscripts, arms, seed)` builds one
  block per cell (canvas, style, manuscript) with the three arms in a seeded order that balances the six
  permutations (24 cells = 4 per permutation) and shuffles block order; `receipt_path` →
  `out/<canvas>/<style>/<stem>.receipt.json`. Resumability unchanged.
- `canvas_bench.py`: subcommand `experiment` with `--styles`, `--arms`, `--outlines <dir>`, `--board-dir`;
  `_request_body` adds `slides_markdown` (the pinned outline's slide strings; `n_slides` omitted) and
  `bench_reference_condition`; receipt gains `style` (already), `arm`, `outlineDigest`, `boardDigest`,
  `referenceConditionObserved`.
- `scripts/canvas_bench_outline.py` (new): stage 0, produces `outlines/<manuscript>-<canvas>.json`
  (`{"slides": [...], "sourceJobId", "backendCommit", "digest"}`). Route (decision D5): run one seed deck
  per (manuscript, canvas) with style `default`, arm A, no `slides_markdown`, then read
  `presentation_generation_jobs.authored_outline` (`models/sql/presentation_generation_job.py:84`, set at
  `presentation_generate.py:1136-1140`) for that job. This is the product outline for that canvas with no
  new backend code path; the seed decks are not experiment data. Alternative if the column is not populated
  for svg jobs (to confirm in the smoke): call the outline endpoint with the canvas fields.
- `canvas_bench_render.py`: `cell_page(cell_dir)` builds one static HTML per cell: the three decks' 6-slide
  contact sheets (2x3, equal display height) stacked in the blinded order, labelled X/Y/Z, no file names.
- `scripts/canvas_bench_blind.py` (new): `shuffle` writes `blind-key.json` (cell → {X,Y,Z} → arm, seeded)
  and `answers-<judge>.csv` templates (`cell, q1_rank_X, q1_rank_Y, q1_rank_Z, q2_rank_X, ...`);
  `unblind` joins the answer sheets with the key, computes per-cell pairwise outcomes, the exact sign test
  per pair (A–B, A–C, B–C) and Cohen's kappa between judges. Stdlib only.
- `scripts/svg_reference_metrics.py` (new, about 300 lines): M2/M3/M4 per slide and per deck from the
  rendered PNGs the bench already produces (`renders` in the receipt), definitions as in the base document
  §4; thresholds are module constants frozen by the calibration step.
- Tests in `tests/unit/test_canvas_bench.py` (plan balance, receipt paths, request body with
  `slides_markdown`, arm mismatch detection), `tests/unit/test_canvas_bench_blind.py` (key/unblind
  round-trip, sign test against known values), `tests/unit/test_svg_reference_metrics.py` (synthetic PNGs
  with a known letterboxed block, a known 3-column text mask, a known empty band).

### 2.5 Metrics and calibration

- **M1 page flag rate** (ink epoch) is a **guard only**. Measured on the 22 pooled-candidate decks (all
  `svg-overlap-ink-v1`, commit `e8475ee6f`, style `default`, arms = A): **1 flagged page of 132** (16:9: 1
  of 42; 1:1: 0 of 48; 21:9: 0 of 42). There is no room for a flag-rate benefit; the safe bound (upper
  one-sided 95% of `p_X − p_A` ≤ +0.10) is the only use.
- **M2/M3** (16-px grid occupancy `U`, largest empty rectangle `LER`, edge void, bbox aspect gap,
  letterbox flag, column-count violation) and **M4 guards** carry the decision with M5. **Calibration
  before any S deck is unblinded**: run the metric script over the 22 existing decks (132 pages, three
  canvases), hand-label 20 slides (letterboxed yes/no; column count) drawn from them, fix the constants
  (cell size, 90 % inside-R, 3-cell active column, 0.85 area ratio) to reach agreement, and commit
  `artifacts/canvas-bench-20260929/reference-experiment/calibration.json` with the labels and the frozen
  constants. If a metric's agreement with the labels is below kappa 0.6 it is dropped from the decision and
  reported only.
- **M5 blind human ranking**: ≥ 2 judges (the user counts as one; both Korean-fluent), 24 cells × 3 arms,
  two questions per cell ("designed for this page shape"; "would present"), ties allowed; exact sign test
  on non-tied cells per pair (24 non-tied cells: 17 wins is one-sided p = 0.032); kappa between judges.
  UI: the static cell pages from 2.4 opened locally, one CSV answer sheet per judge, unblinded only by
  the script after both sheets are in. About 24 screens × 1 minute × 2 questions ≈ 50 minutes per judge.

### 2.6 Smoke (3 decks) — answers the fixed-outline question

Cell (`square-1x1`, `default`, manuscript 01), one deck per arm, using the pinned outline from stage 0 for
(01, square-1x1). Pass conditions, each read from artefacts, not logs:

1. `slideCount == len(slides_markdown)` and the timing receipt's `outline` stage is absent or ≈ 0 ms
   (`generation_timing_receipt.stages.outline`, measured at 124–193 s on the product path).
2. Slide 0 was authored with role `cover`: the checkpoint rows for the job carry `roles[0] == "cover"`
   (`initialize_checkpoints(..., roles=roles)`, `svg_presentation_prepare.py:161`), and the rendered slide
   0 reads as a title page.
3. Each page's text contains its pinned outline's headline (string containment over `slides.svg_content`
   text nodes; catches a silent re-plan of content).
4. Arm evidence: every slide's authoring receipt has `reference_condition` equal to the requested arm; the
   cover slide's `input_image_count` is 1 / 0 / 1 for A / B / C; `style_digest` differs across arms and is
   constant within a deck.
5. Metric script runs on all three decks; `metricEpoch == svg-overlap-ink-v1`; `backendCommit` includes
   the Part 1 rewrite; the snapshot's `provenance.canvas_brief_rewrite` is present.

If 2 fails (slide 0 not a cover): first check that the pinned outline's first entry is the seed run's title
outline (it should be, since the seed used `include_title_slide=True`); if the text is right but the page
is not cover-shaped, that is model behaviour, not a pipeline defect, and the experiment proceeds (the same
outline is shared across arms, so it cancels). If 1 or 3 fails (outline re-run or content re-planned), the
fixed-outline route is abandoned and S runs on the full pipeline (7.2 h at the base document's 6 min/deck).
If 4 fails, fix the switch before anything else.

### 2.7 Time estimate (fixed outlines)

Measured on the 22 pooled decks (6 slides, default style): backend wall mean 281 s, of which outline mean
166 s; non-outline mean **115 s** (1:1 ≈ 98 s, 21:9 ≈ 119 s, 16:9 ≈ 131 s); client overhead including
export ≈ 6 s. Arm C adds one 1280x1280 image per call (estimate: +0.7× the vision tokens of a 1280x720
image, negligible in wall time).

| stage | decks | per deck | serial |
|---|---|---|---|
| Stage 0 outline pinning (seed decks, full pipeline) | 12 | ≈ 4.8 min | ≈ 1.0 h |
| Smoke | 3 | ≈ 2.0 min | 0.1 h |
| S run | 72 | ≈ 2.0 min | **≈ 2.4 h** (2.9 h with 20 % retries/settle margin) |
| Metrics + contact sheets | — | — | ≈ 0.2 h |
| Human ranking | 2 judges | ≈ 50 min each | in parallel |

Total machine time ≈ 3.6–4.2 h serial on the isolated backend (`:8091`), against 7.2 h without fixed
outlines. Build effort is in 2.9.

### 2.8 Decision rule (per outcome)

Safe = M1 guard holds and M4 guards not worse than A beyond the bench's secondary margins (text ink
coverage not > 10 % below A; generation/export failures not above A). Fit benefit = letterbox rate or
column-violation rate down ≥ 0.15 absolute **and** mean `LER` down ≥ 20 % relative (paired sign-flip
permutation, one-sided p < 0.05). Human benefit = sign test one-sided p < 0.05 on question 1, direction
agreeing with fit. X wins over A = Safe and (Human benefit, or Fit benefit with no Human loss).

| outcome | action |
|---|---|
| **C wins** (over A; not worse than B) | Build the boards for all 39 canonical styles with the same renderer and review checklist; use boards off-16:9 only; keep A at 16:9. |
| **B wins** (over A; C does not beat B) | Ship "no references off-16:9" (drop references in `pin_svg_style` when shape ≠ baseline); zero asset cost. |
| **Tie** (no arm wins, none loses) | Keep A. The image is not the driver; with Part 1 already applied, the next suspect is the residual layout vocabulary in class C lines and the composition clause itself. |
| **Both lose** (B and C lose to A) | References carry value boards do not replace: produce per-canvas references, starting from `a4-portrait` (kbs-company-proposal's 0.69 references are the native example), and test that one canvas before any wider production. |
| Per-canvas reversal (an arm significantly worse on one canvas) | Block that canvas only; decide the rest on the pool. |

### 2.9 Threats to validity

- **T1 text anchor**: removed for `common_guidance` by Part 1; the `overrides` anchors (chroma-fold,
  screenshot-blue) are not in S; residual layout vocabulary remains equal across arms.
- **T2 outline provenance**: seed outlines come from arm A default-style runs; because every arm and both
  styles share the same outline per (manuscript, canvas), this cannot favour an arm; it does mean the
  outline was never "seen" by strategic-navy, which matches the product (outline is style-independent).
- **T3 provider drift**: arms interleaved within each cell, blocks shuffled, serial; one backend commit,
  refuse to pool otherwise; a shared-stack incident invalidates the cell.
- **T4 arm B prompt residue**: rule line `:49` and the Part 1 caveat mention references in all arms;
  identical text across arms is preferred over per-arm wording. **T4b**: the design JSON differs across arms
  only in the `digest` hex string; accepted.
- **T5 metric epoch**: all decks `svg-overlap-ink-v1`; calibration decks are the same epoch.
- **T6 board quality**: arm C measures our two boards; if C loses and B wins, one board iteration before
  concluding boards cannot work.
- **T7 metric validity**: calibrated and frozen before unblinding (2.5); dropped if they disagree with the
  labels.
- **T8 judges**: two, one of whom is the user; kappa reported; a third for disagreements only; VLM screening
  never decides.
- **T9 power at S**: 24 cells give one-sided 0.05 at 17/24 on M5; per-canvas results (8 cells) are
  descriptive only. S is a go/no-go; a split result continues to M on the same machinery.
- **T10 fixed outlines hide outline–canvas interaction**: an arm effect that acts through the outline
  (none is hypothesised) is invisible here; the product path would show it.

---

## 3. Implementation stages (both parts)

| # | stage | files | LOC (est.) | tests |
|---|---|---|---|---|
| S1 | Goldens (own commit) | `utils/llm_calls/author_slide_svg.py` (+8/−2 `svg_design_text`), `scripts/capture_svg_design_baseline.py` (new), `tests/fixtures/svg_design_16x9_baseline/design_<39>.txt` (new), `tests/unit/test_svg_design_16x9_baseline.py` (new) | 50 + 60 + fixtures | 45 + 2 cases |
| S2 | Rewrite + caveat | `utils/llm_calls/author_canvas_shape.py` (+6 `shape_word`), `services/generation_core/drivers/svg/canvas_brief.py` (new), `services/generation_core/drivers/svg/project_style.py` (+6/−1), `utils/llm_calls/author_slide_svg.py` (+10 caveat), `tests/unit/test_svg_canvas_brief.py` (new), `tests/unit/test_author_canvas_shape.py` (:236-241) | 60 + 25 + 120 | ~300 parametrised cases; S1 goldens unchanged |
| S3 | Restart shared backend; live check (1.9) | — | — | 2 live decks |
| S4 | Arm switch | `models/generate_presentation_request.py` (+6), `services/svg_generation_admission.py` (+10), `utils/svg_style_adapter.py` (+5), `services/generation_core/drivers/svg/reference_condition.py` (new), `utils/llm_calls/author_slide_svg.py` (+2), `tests/unit/test_svg_reference_condition.py` (new) | 50 + 25 + 80 | 7 cases |
| S5 | Boards (2 styles) | `scripts/style_board_render.py`, `scripts/style_board_spec.schema.json`, `scripts/check_style_boards.py`, `authored_style_boards/{default,strategic-navy}/{board.png,spec.json}`, `manifest.json`, `tests/unit/test_style_board_render.py` | 250 + 60 + 60 | render determinism, size cap, stale detection; user review |
| S6 | Outline pinning | `scripts/canvas_bench_outline.py` (new), `tests/unit/test_canvas_bench_outline.py` | 80 + 40 | file shape, digest |
| S7 | Runner + blind UI | `scripts/canvas_bench_matrix.py` (+40), `scripts/canvas_bench.py` (+40), `scripts/canvas_bench_render.py` (+60), `scripts/canvas_bench_blind.py` (new 120), `tests/unit/test_canvas_bench.py` (+80, coordinate with the pending edits), `tests/unit/test_canvas_bench_blind.py` (new 60) | 260 + 140 | balance, paths, body, unblind round-trip |
| S8 | Metrics + calibration | `scripts/svg_reference_metrics.py` (new 300), `tests/unit/test_svg_reference_metrics.py` (new 120), `artifacts/.../reference-experiment/calibration.json` | 300 + 120 | synthetic masks; 20 hand labels |
| S9 | Stage 0 + smoke on `:8091` | — | — | 12 + 3 decks, criteria 2.6 |
| S10 | S run | — | — | 72 decks |
| S11 | Unblind, analyse, report | `artifacts/.../reference-experiment/report.md` | — | — |

Order: S1 → S2 → S3 → (S4, S5, S6, S7, S8 in parallel) → S9 → S10 → S11. Build effort estimate: S1–S2 half
a day; S4 half a day; S5 one day; S6–S8 one and a half days; total about 3.5 developer-days before S9.

## 4. Open decisions

- **D1** Rewrite unconditional (recommended; same policy as the HTML path) vs behind an env knob.
- **D2** Keep px type sizes unscaled after R1/R2/R3 (recommended; consistent with HTML path and the
  composition clause) vs scale by `width / 1280`.
- **D3** Leave `overrides` untouched (recommended), accepting chroma-fold:34's residual "1280x720 as the
  design reference", vs ask the style owner to reword that YAML line.
- **D4** Caveat conditioned on shape only (recommended; identical rules across arms) vs only when references
  exist.
- **D5** Outline pinning via seed decks + `authored_outline` (recommended; product outline, no backend
  change) vs a direct outline-endpoint call.
- **D6** Golden scope: 39 canonical styles at no-request (recommended) vs also a request-override variant
  per style.
- **D7** Judges: the user plus one named Korean-fluent colleague; whether a third is available for ties.
- **D8** Accept the per-arm `digest` difference inside the design text (T4b) vs exclude `digest` from the
  prompt (would change 16:9 bytes; not recommended).
- **D9** Whether S may be promoted to M on the same machinery if the S result is split (recommended yes,
  same seed procedure, `luxury-editorial` and `a4-portrait` added).

## 5. Self-score and top problems

Score: **7 / 10** — every fact in Part 1 was re-read from code and the catalogue was probed end to end
(admission, aliases, prompt bytes), and Part 2's switch is anchored on the staging path that would
otherwise silently drop it; nothing is built or measured.

1. The fixed-outline route is structurally sound but its cover/content behaviour from markdown is only
   argued, not observed; the smoke (2.6) is the first evidence, and the fallback costs 4 more hours.
2. M2/M3 are still uncalibrated; with M1 at 1/132 the decision leans on them and on two judges.
3. R1/R2/R3 substitute the canvas name and keep px sizes — the same judgement the HTML path made, but on
   720-wide canvases it may read as oversized type; the bench will show it, the design does not settle it.
4. 39 fixtures coupled to reference-image bytes and `SVG_CONTRACT_VERSION` will churn on unrelated
   changes; the recapture procedure mitigates, but each recapture is a review cost.
5. Shared-file coordination: `tests/unit/test_canvas_bench.py` and `canvas_bench_live.py` carry another
   session's uncommitted work; S7 must land after it or rebase on it.
