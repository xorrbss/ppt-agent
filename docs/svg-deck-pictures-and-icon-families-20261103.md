# SVG deck pictures and icon families: what was built (2026-11-03)

Follow-up to `svg-illustration-icon-pilot-20261103.md`, which holds the pilot
measurements and the decisions. This note records what is now in the code,
how to switch it, what was measured live, and what is still open.

## In the code

| area | where | what |
|---|---|---|
| contract | `utils/svg_validation.py` | `stroke-linecap` butt/round and `stroke-linejoin` miter/round admitted; version string unchanged (stored receipts compare it verbatim) |
| flattener | `drivers/svg/icon_flatten.py`, `embed_assets.py` | round caps kept on icons; opacity kept; a filled multi-subpath path becomes one compound linear path (holes survive in SVG, PNG preview and PPTX); `palette=` mode keeps each shape's colour, snapping off-palette paints |
| icon families | `assets/icon_families/<id>/` (nodes.json, manifest.json, LICENSE), `icon_pack/families.py` | `lucide` (1.67.0, 2,019), `tabler-outline` (3.50.0, 4,825, no brand logos), `phosphor-regular` / `-fill` / `-duotone` (core 2.1.1; 1,509 / 1,512 / 1,477, over-budget icons left out and listed in the manifests) |
| icon choice | `utils/llm_calls/select_deck_icons.py`, `assets/offer.py` | one call per deck picks a main icon and optional badge per figure label from the style's family; the fixed prompt prefix (role, rules, icon list) is a cached system prompt on Anthropic |
| style → family | `utils/authored_styles.py` | `icon_family` YAML key; default by category: business → `tabler-outline`, others → `lucide`; `flat-infographic` → `phosphor-duotone` |
| no provider icons | `assets/offer.py` | icons are family picks or bundled library vectors only; sprite-sheet PNGs are never requested or offered |
| model pictures | `utils/llm_calls/draw_vector_illustrations.py`, `assets/vector_offer.py`, `assets/vector_illustration.py` | with `SVG_ILLUSTRATION_MODE=vector|hybrid`: scene pictures, cover/section/closing pictures, step pictures (kind `spot`) for process/cycle/timeline/roadmap pages, before/after pairs; validated, normalised, at most 12 calls per deck |
| plan | `utils/llm_calls/author_infographic_plan.py`, `models/authored_infographic_plan.py` | vector mode: scenes may show an abstract message as a metaphor, carry `depiction` (photo/concept), no scene ceiling |
| authoring | `authoring/author_slide_svg.py` | one rule sentence for kind `spot`; omitted model pictures are reported (`omittedOptional`) and do not fail the page |
| fallback pages | `drivers/svg/fallback.py` | background, text and accent from the deck tokens by contrast; cover-shaped fallback for the cover role |
| vision review | `quality/vision_advisory.py` | on Anthropic the review sends `thinking: disabled` (mapped to the model's lowest setting) so thinking no longer exhausts its 4,000-token answer |

`SVG_ILLUSTRATION_MODE` is an internal knob (`tests/support/internal_env_names.py`),
read when a deck's asset offer is built; a resumed deck replays its persisted offer.

## Live measurement

Six 8-slide decks, one prompt (사내 업무 자동화 도입 계획 …), `claude-sonnet-5-5`, no
image provider, two backends started before the vision-review fix
(`2c9a857f6`): `SVG_ILLUSTRATION_MODE=off` and `vector`. Each backend ran its
three decks one after another, so per-deck time is the gap between completions.

| mode | style | quality pages passed | page flags |
|---|---|---|---|
| off | exec-report | 6/8 | source quality 1, attempts exhausted 1 |
| off | strategic-navy | 8/8 | — |
| off | flat-infographic | 0/8 | design review / deck flow unreviewed 8 (review-side) |
| vector | exec-report | 6/8 | attempts exhausted 2 |
| vector | strategic-navy | 5/8 | attempts exhausted 1, source quality 1, information structure 1 |
| vector | flat-infographic | 5/8 | attempts exhausted 2, source quality 1 |

| | off (3 decks) | vector (3 decks) |
|---|---|---|
| total provider cost | $5.63 | $6.63 |
| illustration stage (plan, icon pick, drawings) | $0.30, 6 calls | $1.20, 21 calls |
| time per deck (completion gaps) | about 260-310 s | about 300-340 s |
| drawings requested / accepted | — | 42 / 42 (5 calls per deck, no retry, mean fill 0.76) |

Reading:

* Every drawing passed validation; covers, step pictures and before/after pairs
  rendered in the deck's own colours (the navy deck's pair, the flat deck's scene).
* Pages that ended as fallback or flagged by the author's own checks went from 2
  of 24 (off) to 8 of 24 (vector): pictures take room and add elements the author
  must lay out. The flat-infographic off deck's 8 flags are the vision review's
  truncated answers (fixed by `2c9a857f6`), not page failures.
* Three decks per arm is a small sample, but by the rule set before measuring
  (default on only if the quality gate does not get worse) the default stays
  **off**. Next steps before turning it on: give pictures a reserved area in the
  page composition (or skip drawings on text-dense pages), and re-measure with
  the vision-review fix in place.

### Re-measurement with cover and scene pictures only

Same prompt and styles, code at `d268f1fd4` (vision-review fix, group font
push-down, `SVG_ILLUSTRATION_PARTS` default `cover,scene`).

| mode | exec-report | strategic-navy | flat-infographic |
|---|---|---|---|
| off | 6/8 (attempts exhausted 2) | 7/8 (attempts exhausted 1) | 8/8 |
| vector (cover, scene) | **deck failed**: both header candidates' representative page failed static quality (`asset_icon_omitted`, `source_text_paint_occluded`) on an icon-only page | 6/8 (attempts exhausted 1, design density/structure 1) | 5/8 (design density 2, attempts exhausted 1) |

* 4 drawings requested, 4 accepted (3 covers, 1 scene). Cost: off $4.62, vector
  $5.23 for the three decks (vector includes the failed deck's partial spend).
* Author-level page failures are now level (off 3 of 24, vector 2 of 16), but
  the vision review flagged 3 vector pages for density balance and none with
  pictures off. The failed deck was not caused by a picture: its representative
  page carried only icons. Decision: the default stays **off** for now.
* The deck failure shows the structural gap again: when every representative
  page fails static quality, for any reason, the whole deck fails. Across both
  arms icon omission hit 1 of 21 icon pages outside the header step, but 4
  header-candidate attempts in the failed deck.

### Header candidates judged by their header (`871053c58`)

The failed deck above showed that one representative page's body defects could
fail a whole deck. Change:

* When no header candidate passes outright, a candidate whose representative
  page passed every header check (no `svg_common_header_*` failure, a passed
  `quality.common_header` measurement, a valid last drawing, the author's body
  kept) is admitted. Its body is promoted and the regular page loop measures,
  repairs and rescues it like any other page. When a candidate passes outright,
  selection works exactly as before.
* A candidate page rescued by the fallback keeps `author_body_svg` and its
  attempt identity, so it can still be promoted.
* The selection input marks such a candidate with `body_checks`, and the
  selection rules say to judge the header and its fit, not those body defects
  (prompt `svg-common-header-screen-choice/v2.2`).
* `svg_common_header_representative_pages_ineligible` is a bounded failure:
  when no candidate qualifies at all, the deck takes the reduced header instead
  of failing.
* Not changed: the representative page choice. Its risk counts
  `ctx.manifests[index]`, but only raster offer slots enter it; deck icons and
  model drawings are vector slots and do not, so the icon work did not bias it.

Re-measurement with this code, same prompt and styles (cover and scene pictures):

| mode | exec-report | strategic-navy | flat-infographic |
|---|---|---|---|
| off | 6/8 (density 1, attempts exhausted 1) | 6/8 (source quality 2) | 8/8 |
| vector (cover, scene) | 5/8 (design 2, attempts exhausted 1) | 7/8 (density 1) | 8/8 |

* No deck failed; all six published a designed header. In every deck at least
  one candidate passed outright, so the new header-only admission did not need
  to run (one vector candidate had a body-only `text_overlap` with its header
  passed, which is the case it admits). Its behaviour is covered by unit tests
  (`test_svg_header_scoped_eligibility.py`, `test_svg_terminal_rescue.py`).
* Pages passed: off 20/24, vector 20/24. Cost: off $4.87, vector $5.30 (the
  illustration stage $0.33 vs $0.51). 3 drawings requested, 3 accepted (one
  after a retry for invalid XML).
* Combined with the previous re-measurement, vector is level with off on page
  failures but has added design-density flags in two of the three runs. The
  default stays **off**; turning it on is now a product decision rather than a
  stability blocker.

## Open

1. **Group font attributes — decided 2026-11-03.** A model sometimes writes
   `font-family` on a `<g>`; the contract rejected it and, on a header
   representative page, the whole deck failed. Decision: font-family/size/weight
   and text-anchor written on `svg`/`g` are pushed onto the texts (never `fill`),
   in the producer and in `parse_author_response` alike (`97200520f`). A deck no
   longer fails outright when every representative page fails (`871053c58`).
2. **Pictographs** (counts drawn as rows of icons) are not implemented.
3. **Icons the author does not place** still fail their page (`asset_icon_omitted`);
   seen on two pages of the first live vector deck.
4. The bundled 300-icon library under the repository root `assets/` is not copied
   into the Docker image; the icon families live under `servers/fastapi/assets/`.
5. Pre-existing red gates, unchanged by this work: `ruff check .` (25 findings in
   `svg_templates` and others), and the long-standing svg unit-test failures.
