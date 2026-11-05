# SVG template pages: first-draft failures (2026-11-04)

Backlog item 9 (`docs/svg-template-handoff-20261104.md`, Next 2): fewer template pages whose first
draft fails, without loosening a check that protects real quality. Code: `5875be5bb`.

## Classification

Data: the checkpoints (`authored_slide_checkpoints.svg_checkpoint.attempts`) of the 25 template decks
stored in this session's databases (250 pages, the real template deck, the fictional structured input,
10 pages each): the policy-off baseline (3 decks), connectors-only and connectors-direction (3 + 3),
automatic frames (3), tables and charts (7), component style (6). Every page that needed a second
attempt records the first draft's failure as the second attempt's `attempt_cause`; the first draft's raw
response and quality failures are stored with it, so each case was looked at in the raw SVG.

52 of 250 first drafts failed (79 % first-try; the policy-off baseline alone 28 of 30).

| Cause (`attempt_cause`) | Pages | Role | What the raw drafts show |
|---|---|---|---|
| `svg_template_font_unavailable` | 10 | cover 10 | The cover frame has one face, Pretendard 700. Every draft wrote some texts with no weight, `normal` or `400` (`bold`/`700` itself was accepted). On 3 covers the retry failed the same way (3 of the 15 pages whose retry failed too). |
| `svg_template_body_outside_allowed_region` | 14 | body 14 | 3 only a whitespace text (`<text font-size="1"> </text>` spacers), measured by the browser as an empty box at (0, 0); 4 a closing note whose baseline sat at y 860-868 under a bottom edge of 844; 4 the last card or panel ending at y 850-857; 2 chart value labels 4 px past the right edge (table plus chart page); 1 a zero-length line at y 850 |
| `svg_contract_positive_required` | 4 | body 4 | A zero-height `rect` (`height="0"`, spacers and accent bars) |
| `text_overlap` | 3 | body | Real overlaps |
| `required_text_missing` | 3 | cover 2, body 1 | Both covers wrote the source date "2027년 3월 ~ 2028년 2월" as "2027.3 ~ 2028.2"; one body page dropped a count |
| `density_balance` (vision review) | 4 | cover 2, body 2 | Design review |
| `information_structure` (vision review) | 3 | cover 1, body 2 | Design review (component style decks) |
| `physical_font_too_small` | 2 | body | `font-size="14"` under a minimum of 14.1581 px given as "Body text minimum: 14.1581 CSS px" |
| `native_requested_table_missing` / `_chart_missing` | 2 / 2 | body | Tables and charts policy on |
| `svg_contract_unsupported_attribute` | 2 | body | `display="none"` on a whitespace text, a misspelt `end-anchor` |
| `svg_template_font_glyph_missing` | 1 | body | A stray CJK ideograph in Korean text |
| `text_hidden` | 1 | body | |
| `writing_policy_visible_symbols` | 1 | cover | A pipe separator |

Arrowheads (item d): the 250 final pages hold 107 small triangles; 94 sit in the gap between two boxes,
on 26 pages as rows or columns (mostly the connector and tables-and-charts decks).

## Fixes

Deterministic (they cannot change a page that was already accepted):

1. **Missing weight of a face the page has** (`frame_composition.compose_frame_svg`,
   `frame_fonts.matched_font_asset`): the text gets the face the browser's font matching draws for it
   anyway (CSS Fonts 4: 400 on a bold-only page draws 700; 900 on a 400/700 page draws 700; 300 draws
   400), written on the text so page, measurement and PPTX agree. Never for a bold weight (600+) whose
   only match is lighter: a browser synthesises bold there, so the page keeps failing with the
   existing message. A family the page lacks still fails. The frame's face list is not widened (a
   cover keeps only the faces its source page uses).
2. **Whitespace text is not body paint** (`generation.apply_template_body_quality`): a text with no
   non-space character draws no glyph (the contract admits no text decoration); it no longer counts as
   paint outside the body. Real paint outside the body still fails.
3. **Zero-size shapes are dropped** (`svg_text_defaults.drop_zero_size_shapes`): SVG renders nothing for
   a `rect` with zero width or height or a `circle` with radius 0, not even a stroke. The producer and
   `parse_author_response` both drop them (receipt `zero_size_shapes_dropped`); a negative or
   non-numeric size is left to the validator. Every accepted page is a no-op (it could not hold one).

Prompt facts (what the author was not told precisely):

4. **Body edges in whole pixels**: the author prompt gets `template_body_edges`
   (`{"left": 68, "top": 188, "right": 1212, "bottom": 844}` on the measured deck) next to
   `template_page`, whose `body_region` was `y 187.0049..., height 656.9950...`. The template rules say
   that every shape and every text line (glyphs reach about a quarter of the font size below the
   baseline) ends at or above `bottom`, and to plan the rows before drawing. `template_page` itself is
   unchanged (it is a digest input compared at publication and on page edits).
5. **The figures the page is checked for**: `required_literals`, the exact list the page's
   `required_text_missing` check uses (`_required_numeric_claims`), with a rule to show each exactly so.
6. **A fractional text minimum**: "Body text minimum: 14.1581 CSS px (write font-size 14.5 or larger; a
   size rounded down below the minimum fails)". An integer or half-pixel minimum keeps the old sentence.
7. Template rules: "a text without font-weight is weight 400, so on a page whose font_faces have no
   weight 400 give every text one of the listed weights" (item 1 is the safety net).

Arrowheads (item d), an added check, only with the connector policy on
(`CONTENT_STRUCTURE_POLICY_SCOPE` in scope and `connectors` in `CONTENT_STRUCTURE_POLICY_PARTS`):

8. `generation_core/drivers/svg/connector_direction.py` flags `connector_arrowheads_reversed` (a layout
   failure: the page is kept and flagged if the retry fails too) when **at least two** arrowheads lie on
   one row or column, each a small triangle with an axis-aligned base in the gap between two boxes, and
   **every** one of them points to the box that is first in reading order **and** whose text comes first
   in the page's source. A single backward arrowhead (a loop back, a cycle's last edge), a line with any
   forward arrowhead, a stack whose source lists the bottom layer first, and boxes whose text the source
   does not contain all pass. The failure lists each arrowhead's points and the two items, and the
   repair instruction says where the apex goes. On the 250 stored final pages (and every stored draft)
   it flagged exactly the two reversed pages found by eye earlier (a five-stage row, a four-layer stack)
   and one more that had not been noticed (a five-activity row on a tables-and-charts deck, all four
   triangles pointing left); nothing else. Without the policy arrows are rare (0 polygons per baseline
   deck), so the check is not run there.

Not changed: overlaps, design-review causes, the stray-ideograph and misspelt-attribute drafts (real
model errors that a retry repairs), and the 4 drafts whose cards end below the body (item 4 targets
them by prompt only; moving drawn geometry would risk new overlaps).

Found during the live run and fixed after it (`compose_frame_svg`):

9. **Generated body ids skip taken ones.** A repair is shown its previous composed page, whose id-less
   nodes had become `body-node-N`, and copied some of those ids onto a new drawing whose nodes sit at
   other positions (icons are flattened into several nodes). Composition then gave an id-less node a
   generated id equal to a copied one, the composed page failed `svg_contract_invalid_id` (duplicate)
   and the page fell back. A generated id now skips ids the drawing already uses
   (`body-node-N-1`, ...). Reproduced in a unit test; not yet measured live.

Unit tests: `tests/unit/test_svg_template_first_draft.py` (synthetic, 24 tests). The existing font error
test now uses a family the page lacks (a 900 weight is drawn with the 700 face).

## Live measurement

Offline, with the current code and no model calls (`compose_frame_svg` with the stored cover frame;
icons replaced by plain rectangles of the same box): all 10 cover drafts that had failed
`svg_template_font_unavailable` now compose (their regular texts take the 700 face), and 3 of the 4
`svg_contract_positive_required` drafts compose; the fourth then fails `svg_contract_invisible_text`
(a whitespace text with `fill="none"`, left as it is). The browser checks after composition were not
replayed. The arrowhead check over the stored data is described under item 8.

Live: the same request as the baseline (`S/eval_request2.json`, the real template deck, 10 pages,
`text-heavy`, frame map), policy knobs unset, the evaluation tool with the judge (same references and
model), label `first-draft`. **Only 2 of the planned 3 decks completed**: the third failed at its first
page call because the provider account's credit ran out (`invalid_request_error`, credit balance too
low), so the `connectors-checked` batch (N=3 with `CONTENT_STRUCTURE_POLICY_SCOPE=template
CONTENT_STRUCTURE_POLICY_PARTS=connectors`) could not run either. Two decks are below the N >= 3 rule;
read them as a check that nothing broke, not as a measurement of the effect.

| Metric | Baseline (N=3, before this change) | first-draft (N=2) |
|---|---|---|
| First draft accepted | 9, 9, 10 (9.33) | 8, 9 (8.5) |
| Fallback pages | 0, 0, 0 | 1, 0 |
| Gate passed / flagged | 9/1, 9/1, 9/1 | 7/3, 9/1 |
| Judge overall | 3.46, 3.40, 3.52 (re-judged 3.52, 3.42, 3.54) | 3.38, 3.48 |
| Judge structure / density / hierarchy / tone / visuals | 3.47 / 2.87 / 3.90 / 4.13 / 3.10 | 3.45 / 2.80 / 3.90 / 4.15 / 2.85 |
| Words / numbers kept | 73-76 % / 100 % | 74-75 % / 100 % |
| Seconds per deck | about 420 | 450, 454 |

* **Covers: both first drafts were accepted**, written entirely in `bold` / `700` (earlier: 10 of 25
  covers failed on the font, 1 of 3 in the baseline). The composition fallback (item 1) was not needed;
  the rule sentence (item 7) is the likely reason, but two covers cannot show it.
* The three repaired first drafts: a stray CJK ideograph in a Korean word (the same word on the same
  page as one earlier draft; a real model error, repaired), seven long dashes (`writing_policy_visible_symbols`,
  repaired), and **a last card ending at y 856 under a bottom edge of 844** although the prompt now gave
  `template_body_edges`: the prompt fact (item 4) did not stop this case. Its retry failed on the
  duplicate id of item 9 and the page fell back (the only fallback). No zero-size shape or whitespace
  spacer appeared in these two decks.
* Judge and timing are within the baseline's spread (the judge moves about 0.2 per axis between
  calls); visuals 2.85 against 3.10 is the largest difference and rests on two decks.

## Uncertainties and next steps

1. Measure again (N >= 3, policy off) once the provider credit is restored; the target is at least 9.5
   of 10 first drafts accepted and no fallback. Then `connectors-checked` (N=3) to see the arrowhead
   check catch or prevent a reversal live; it is unit-tested and checked on stored pages only.
2. Cards that end below the body remain the most common geometric first-draft failure after the
   fixes (4 of 250 earlier, 1 of 20 here). A prompt fact is not enough; a deterministic option is to
   measure the overflow and, when only the last row passes the bottom by a few pixels and nothing below
   it would collide, report the exact overflow in the repair hint (it already names the elements and
   boxes) rather than moving geometry on the server.
3. `required_literals` and the text-minimum clause did not get a chance to show an effect (no such
   failure in two decks).
4. A whitespace text with `fill="none"` still fails the contract (`svg_contract_invisible_text`); one
   draft in 250.

## Second round: 24 decks of the policy measurement (2026-11-05)

Data: the 24 decks of the paired default-decision measurement (databases s5a/s5b/s5c: policy off,
connectors only, tables only; 4 fictional inputs x 2 templates, 240 pages). 51 first drafts failed
(189/240 first-try). Every failed draft was read in its raw response and its stored browser metrics.

| Cause | Pages (A / B) | Role | What the drafts show |
|---|---|---|---|
| `native_requested_table_missing` | 13 (7 / 6) | body | Not in scope here (projection: header band outside the group, grid undetermined) |
| `text_overlap` | 9 (2 / 7) | body | 15 pairs, all on glyph ink: 6 a native table's header labels drawn twice (outside the group and again inside on the same baseline, 0-6 px apart); 4 a text wider than allowed (60 px `약 46%` 3.3 px into the next text, 38 px figures 12-17 px, a 16 px header 24 px past its column); 3 a note panel drawn over a chart's labels; 1 two combo-chart value labels; 1 two stacked lines (14 px over 38 px) 1.7 px too close |
| `native_requested_chart_missing` | 6 (4 / 2) | body 5, cover 1 | Tables-only arm |
| `required_text_missing` | 4 (2 / 2) | body | Tables-only arm only: chart values written as `342` under a `(억 원)` caption while the page is checked for `342억`; one degenerate draft |
| `svg_template_body_outside_allowed_region` | 3 (+1 with an overlap) | body | All horizontal this time: labels centred 1-2 px left of the right edge, a closing text 7 px past right, a 38 px figure 59 px past right, a rule starting at x 60 (left edge 64) |
| `svg_source_image_unknown_slot` | 3 | body | An offered icon written as `<image ... width="0" height="0">` |
| visual review (structure / density / hierarchy) | 5 | | Design review |
| `svg_contract_invisible_text` | 2 | body | A `.` spacer text with `fill="none"` |
| `text_hidden`, `small_body_text` | 1, 1 | body | A `.` spacer with `opacity="0"`; a `.` spacer at `font-size="1"` |
| `svg_contract_linear_path_only` | 1 | body | `<path d="M940 312 A0 0 0 0 1 940 312" fill="none"/>` (no stroke) in a donut; the donut itself uses `stroke-dasharray` |
| `svg_contract_unsupported_element` | 1 | body | An empty `<style></style>` |
| `text_off_canvas` | 1 | body | A native chart group with `opacity="0"` and its title at (0, 0) |
| `source_text_paint_unproven` | 1 | cover | |

The overlap detector had no false positive: every pair is on measured ink, and three of the closest
(3.3, 1.7 and 16 px) were checked in a fresh render; the glyphs touch.

Fixes (commits `e7e01eefb`, `6741be92d`):

1. Deterministic, in the producer and `parse_author_response` (receipts
   `unrendered_marks_dropped`, `duplicate_table_headers_dropped`), no-ops on every accepted page:
   zero-size `<image>` joins the zero-size shapes; a `<path>` with neither fill nor stroke, an empty
   `<style>`/`<defs>`, and a text without any letter or digit that draws nothing (no fill and stroke,
   or with a non-space character `opacity`/`fill-opacity` 0 or a size of at most 2 px) are dropped;
   a header label written outside its `data-native-table` group whose text equals a header cell
   drawn inside the group on the same baseline (1 px, `translate` included) is dropped.
2. Prompt facts: `template_text_metrics`, per face of the page's frame, read from the font bytes:
   widest Hangul syllable and digit, average Latin letters, space, common figure symbols, and glyph
   ink above and below the baseline (Pretendard 700: Hangul 0.864 em, digit 0.66 em, ink 0.80 / 0.21
   em). The rules say to work a text's width out before placing it and keep 8 px clear of its
   container, the next text and the right edge (with the anchor), and how far apart stacked
   baselines must be. The `required_literals` rule now names a unit left to an axis or column caption.
3. Repair messages: `text_overlap` hints carry witnesses (the two texts, sizes, measured ink boxes,
   overlap in px, `same_text`) in whatever branch wrote the hint, staying one JSON object when the
   hint is one; body-outside failures name the text and `past_edge_px` per edge.

Offline replay (no model calls): each stored failed first draft through `parse_author_response`,
declaration extraction, icons as plain rectangles, `compose_template_page` with the stored page
context, the browser render with the template fonts, the template body check and the paint
occlusion check (native requirements, vector icons and the visual review are not replayed).
The geometric failures that were not addressed are all reproduced, which validates the harness.
Now passing every replayed check: 9 drafts (3 zero-size icons, 2 invisible and 1 hidden `.`, 1 tiny
`.`, 1 empty `<style>`, 1 duplicated-header overlap page); one more overlap page loses its two
duplicate pairs and keeps its width pair; the other duplicated-header page also failed its native
table, which is unchanged. The `linear_path_only` draft now fails on `stroke-dasharray` instead.
The prompt facts cannot be replayed.

Live check (policy off, N=1 per cell, not a measurement): template B x unstructured report 10/10
first try (baseline 10/10); template A x data report 6/10 (baseline 8/10): a 16 px sentence 70 px
past right (the width rule did not prevent it), a degenerate draft with an empty table group, a
native table and a visual review failure. Measure N >= 3 per cell before reading an effect.
