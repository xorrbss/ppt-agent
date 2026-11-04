# SVG user template: generality check (2026-11-04)

Registration and generation had been checked on one real customer deck. This note records the check on fictional
synthetic decks (python-pptx) and on three public decks (`docs/artifacts/ppt-master-catalog`), what broke, and what
was fixed. No customer text is used. Tool: `scripts/svg_template_eval/register_template.py` (framed, automatic font
substitution) against a local backend.

## Synthetic decks (4 pages each, 16:9)

| Deck | Registered | Time | Header fields found | Wrong detection |
|---|---|---|---|---|
| a. title in a title placeholder, band | yes | 30 s | title | none |
| b. title 14 pt in a band, 8 pt varying label | yes (after fix) | 29 s | title (before: none) | label correctly stays page content |
| c. no band, title on white, rule | yes | 29 s | title | none |
| d. band, title, "number > chapter" group | yes | 47 s | title, breadcrumb, breadcrumb_number | none |

Findings: placeholders (a), no band (c) and the path group (d) already worked. A title under 18 pt (b) was missed
(`_MIN_TITLE_SIZE_PT`); now 12 pt at the very top of the page (bottom edge within 15 % of the height), 18 pt anywhere
in the header; a bare page number is never the title. The restriction to the top stops a 16 pt body heading from
becoming the title (an existing component-style test caught that). Not covered: chapter_* fields (only decks with one
layout per chapter have them).

## Public decks (15 pages, 16:9, one layout "Title Slide" for every page)

| Deck | Registered | Time | Fonts substituted | Frames (default selection) |
|---|---|---|---|---|
| executive report | yes after fixes | 74 s | Arial, Cambria -> Pretendard | cover + 3 body, title dynamic on body |
| AI technology proposal | yes | 63 s | same | same |
| data analysis report | yes | 70 s | same | same |

All three first failed to publish. Causes, all general:

1. One layout for every page meant the default selection analysed page 1 only, a cover without furniture
   (`svg_template_fixed_furniture_missing`, publish refused). Now a single-page selection of a longer deck is topped up
   to four evenly spaced pages.
2. PowerPoint writes a different `a16:creationId` into every shape of every slide, and unwrapped text boxes carry a
   width measured per page. The repeat key differed on every page, so no furniture (label, hairline, footer) was ever
   recognised. Both are now ignored in the key.
3. A full-slide rectangle 240 EMU short of the slide was not the page background (tolerance 0.01 px); the dark
   background was lost and white text would sit on white. Tolerance is now 1 px.
4. An unwrapped text box failed with `svg_template_text_overflow` when the substituted face is wider; it now grows
   to its text. A footer box hanging 2 px below the page failed adaptation (`..._fixed_element_does_not_fit`); text
   boxes are now clipped vertically as they already were horizontally.
5. A font substitution names one weight per source family, so bold text registered the regular face only and every
   generated page failed on `font-weight 700`. Bold now maps to the substitute's bold, and a frame's manifest holds
   every face of the families it draws with.

## Generation (executive report, 6 pages, `template-redesign`, pptx, canvas 12192000 x 6858000 EMU)

Only one deck was made: the key's credit ran out before the second. That run used the template registered before fix 5:
gate 0/6 passed, 5 fallback pages, all 6 first drafts rejected on font weight 700 (the repair cost a face). Rendered
pages show the template look kept (dark background, header label, hairline, footer, dynamic title in white); no wrong
header text, no overlaps; five pages are plain bullet lists (fallback), one has cards. Source numbers kept 100 %,
words 75 %. The re-registered template (fix 5) was not generated on: **to do**, one run on each of the two decks.

## Open

* Fix 5 and its effect on first-draft success are unmeasured.
* A cover without any own furniture is blocked by design; select body frames only (the dev script does).
* A substitution target without a bold face is not checked by the registration API.
* Serif source titles (Cambria) become Pretendard; the look changes.
