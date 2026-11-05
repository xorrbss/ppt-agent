# Masked template body renders for the template-redesign author: design (2026-11-05)

Status: design for review, no product code. Base `7ec3fe48` (the template A visuals branch merged with `main`).
Measurements behind it: `docs/template-a-visuals-progress-20261105.md`. The customer deck (template A), its renders
and anything derived from its text stay out of the repository; this note gives counts only.

## 1. Problem and evidence

* The author of a template-redesign page never sees the template. Its frozen style snapshot carries no image:
  `references` and `reference_examples` are empty because `services/svg_templates/storage.py` (profile summary) drops
  `references` from the style projection at publication (they were full-text renders of pages 1, 2, middle and last,
  dropped to shrink job rows). What the author gets is text: the composition grammar, one or two exemplar outlines
  (`services/svg_templates/body_composition.py`) and the frame facts.
* Prompt-level levers did not move density or visuals on template A (judge v2, 4 inputs x 3 runs, paired):
  outline sections raised hierarchy by 0.38-0.51 and structure by 0.19-0.25, but density stayed 2.46 -> 2.50 / 2.55
  and visuals 2.18 -> 2.15 / 2.23; neither the body-vocabulary line nor model-drawn step pictures changed them.
  The judge's empty share stayed at 37 % per page (measured blank share 0.42-0.43; the template's own pages
  0.15-0.37), and on 74-80 of 108 pages per arm the visuals reason was "no charts, diagrams or figures like the
  anchors" or "only small line icons".
* The gap is visual: template A's 100 body pages have a median of 56 shapes, page-specific pictures on 47 pages,
  flow shapes on 61, tables on 47 and charts on 11; the generated pages draw about 14 rects and 37 texts each, almost
  no polygons and no pictures (image generation off). The template's pages score about 4.4 on the same judge, the
  generated ones 2.7-3.0.

Hypothesis: an image of the template's own body page with the same content kinds conveys fill, scale, component mix
and picture placement that the text outline does not, and moves density and visuals.

## 2. Where the renders come from

What registration already has (`services/svg_templates/registration.py`, framed mode):

* A PDF of the whole source deck: on Windows the PowerPoint capture (`source_pdf_worker.capture_source_pdf`, glyph
  preview path); on Linux the LibreOffice conversion in `source_review._render_source_libreoffice`, which converts the
  whole deck with the package fonts and substitutions but rasterises only the frame pages.
* Rasterised frame pages (at most 12 representative pages, one per layout), stored as `published` artifacts for the
  human review. They are unmasked and are not body exemplars; they must never reach a prompt.
* The body composition (`extract_body_composition`, every content page) and its exemplars: one page per recurring
  combination of content kinds, at most 10, each with `source_page` and its body region size.

To add (one step after `extract_body_composition`, inside the same registration operation):

1. For each exemplar page, take that page of the deck PDF, mask its text (section 3), render it at canvas scale and
   crop it to the page frame's `body_region` (header band, footer, logos and page number are frame furniture and are
   cut away). Template A's body region is about 1144 x 656 px.
2. Store each crop as a PNG artifact (`retention_class="published"`, bound to the template with `retain_artifacts`
   like the review images) and record on the exemplar an optional `render` reference: artifact id, sha256, width,
   height and a mask receipt (version, text objects masked, residual characters 0, OCR result, pictures masked). No
   free-text field, so the "no source text in the package" test still holds (ids and hashes are added to its
   allow-list).
3. The field is omitted when absent: packages without renders keep their digests. A new registration's package digest
   changes (it is a new package anyway).

Size and time, from a prototype on template A's PDF (scratchpad only, seven reference pages): 296-552 text objects and
1,019-1,761 characters per page, 0 characters left after masking; a full-page PNG at 1280 px is 110-206 KB and takes
1-2.6 s per page. Ten body crops: about 1-1.5 MB of artifacts and 10-25 s of registration time; the package grows by
ten small references.

Templates registered before this change have no renders and must be registered again (templates A and B in both
measurement databases; 5-8 minutes each). Generation degrades to today's behaviour for them.

## 3. Text masking

**Method: remove the PDF's text objects and paint a bar in their place**, before rasterising. With `pypdfium2` (already
a dependency): for every text page object take its bounds and fill colour, insert a filled rectangle of the same
width and colour covering the middle 60 % of its height, remove the text object, regenerate the content stream,
render. Each line of text becomes a solid bar of its own colour, length and height, so the page keeps its type
ladder (bar height), colour roles, alignment, line count, table and chart geometry, icons and pictures.

Rejected alternatives: rewriting the PPTX runs to filler characters (chart caches, embedded workbooks, SmartArt data
and data labels hold text and numbers too, and every one would need its own rewrite); masking an unmasked render with
boxes from the PPTX shape geometry (autofit and wrapping move the drawn text, chart labels are missed, and nothing
proves the result is clean).

**Why**: the rule of the body composition is that no source text can reach a prompt. A template is the owner's
customer document; its wording must not appear in, or be paraphrased into, the decks generated from it. The author
rules already say "do not copy reference-image text", but a prompt rule is not a guarantee; an image with no text is.

**Verification, at registration, per page (fail closed: a page that fails gets no render, registration continues):**

1. Deterministic: the masked page's text layer has 0 characters (`PdfTextPage.count_chars() == 0`).
2. Raster content: text inside a picture (a screenshot, a logo with words) has no text layer. Where the tesseract
   runtime is present (`kor+eng`, the one `icon_pack/checks.py` and `authored_quality_ocr.py` use), OCR the masked crop
   and fail the page on a Hangul run of two or more syllables or a Latin word of three or more letters above the
   confidence threshold, calibrated on template A's pages (bars and pictograms produce stray glyphs; the icon-pack
   calibration found noise up to four Latin letters). Where it is absent, pictures larger than an icon (both edges over
   about 120 px) are replaced by a flat block of their mean colour, and the receipt says `ocr: unavailable`.
3. Tests with no customer data: a PDF built in the test with `pypdfium2` (text objects with secret words, a picture, a
   rectangle) must come out with 0 text characters, the secret words absent from every stored string, bars where the
   text was and the rectangle and picture untouched; an OCR test runs when tesseract is installed.

**What is lost**: the words and numbers themselves, font weight (a bold and a regular line of the same size look
alike), the difference between a figure and a word (a large KPI number becomes a thick short bar), and, without OCR,
the larger pictures. Kept: everything the judge's density and visuals axes look at (fill, blank share, component
kinds, chart and diagram shapes, icons, pictures, scale).

## 4. Selection and prompt

* **Which pages**: the renders are of the exemplar pages, so a page's renders are the one or two exemplars that
  `choose_exemplars` already picks for it by `content_kinds` (and, with the sections clause now on by default, by
  section count). Image k is exemplar k; an exemplar without a render keeps its text outline only. Body frames only,
  not covers, sections or source-preserving pages, exactly where exemplars are sent today.
* **Image size**: the body crop scaled to at most 1024 px on its long edge (about 1024 x 587 for template A), PNG.
  About 800 input tokens per image on Anthropic (width x height / 750); one or two images per page.
* **Where**: in the per-page user message, after the page JSON, as a `TEMPLATE BODY RENDERS` block that binds each
  `image_index` to its `template_exemplars` entry, next to (not inside) the existing design-example evidence, so the
  role-routed style references and their receipts are untouched. One rule sentence joins the template rules
  (`body_author_rules`): the images are masked pages of this template; bars stand for text lines (height for size,
  colour for role); match their fill, scale, component mix and picture placement with this page's own content; never
  copy positions, counts or arrangement; never invent content to fill a shape. The rule is deck-stable, so it sits in
  the cached system block of profile v9/v10 (#125); the images vary per page and stay in the user message, which is
  never cached (Anthropic system blocks take text only). The cached prefix of a deck is therefore unchanged in
  structure; it differs between the knob's arms by one sentence.
* **Calls**: every author call of a body page, first draft and corrections (a design rewrite recomposes the page).
  The design review does not get them (left for later).
* **Receipts**: the image digests join `reference_image_digests`; the page receipt gets
  `templateBodyRenders: {offered, sent, missing}`. Prompt only: no digest input of the page, so a stored page replays.
* **Cost** (estimate): about 9 body pages x 1.15 author calls x 0.8-1.6k tokens = 8-17k input tokens per deck, under
  $0.05 at the measured model's price, and perhaps 1-2 s more per call. Rendering costs nothing at generation time (artifact
  read, cached per process by sha256).

## 5. Safety and failure

* Internal knob `SVG_TEMPLATE_BODY_RENDERS` (`on` / `off`, default off), accessor in `utils/get_env_concurrency.py`,
  listed in `tests/support/internal_env_names.py`. Its value is frozen into the template-redesign style snapshot at
  admission (a digest input of new decks only), so a resumed, retried or remade page of the same deck sees the same
  setting (the lesson of the visual-guidance review). It needs `SVG_TEMPLATE_BODY_EXEMPLARS=on` (the default), since
  the renders follow the exemplar choice.
* Fail open at generation: a template without renders, an artifact that cannot be read or whose sha256 does not match
  sends no image and records `missing`; the page proceeds as today. Fail closed at registration: a page whose masking
  cannot be verified gets no render.
* No effect on non-template decks or on style-only templates (no body composition). The frame review renders and the
  dropped style references stay out of every prompt.
* Budget: at most two images per page and 1024 px each, under the author's existing image limits.

## 6. Measurement plan

* Register templates A and B again on fresh measurement databases with this code; check that every exemplar of A has a
  render and that the stored renders pass the leak checks.
* Paired matrix on template A, 4 inputs x 3 runs per arm: control = current defaults (sections on), variant = control
  + `SVG_TEMPLATE_BODY_RENDERS=on`. A fresh control is needed: `main` changed the author prompt (profile v10,
  caching, chart literals) since the earlier baseline. About $25 per arm plus judging.
* Judge anchors: if an exemplar page with a render is one of the judge's score-5 anchors (slides 9 and 66 today),
  judge both arms with two other dense body pages as anchors, so the author is not shown the judge's answer.
* Signals: judge density and visuals (primary), measured blank share and judged empty share, component kinds and the
  share of visuals reasons naming missing charts, diagrams or figures, counts of polygons, paths, pictures, icons,
  tables and charts per deck, first drafts accepted, fallback pages and repair causes, words and numbers kept, input
  tokens and seconds per page, and a template-text leak count (generated slide text against the template's own text,
  computed locally outside the repository; must be 0 in both arms).
* Decision: propose default on if density or visuals improve beyond the 0.2 threshold in at least 3 of 4 cells, first
  drafts do not fall by 0.5 or more, fallbacks do not rise by 0.5 or more, numbers kept do not fall and the leak count
  is 0. Otherwise keep it off and record why. Template B: count metrics only (first drafts, fallbacks, tables, flow
  shapes), 2 inputs x 3 runs, since its judge is not calibrated.

## 7. Effort and open questions

Effort: masking, rendering, verification and storage at registration with tests about 1.5 days; package model,
retention and generation-time fetch, prompt block, receipts and knob about 1 day; the leak metric in the eval tool
half a day; re-registration and the two arms about 4 hours wall time and $50-60 of provider calls.

Open questions for the user:

1. Pictures that may carry words: keep them only when OCR reads no text (needs tesseract `kor+eng` on the registering
   server; this VM has none), or always replace large pictures by flat blocks (safe everywhere, but loses the
   illustrations that the visuals axis rewards)?
2. Should registration always store renders (data only, used only with the knob on, so templates registered from now
   on need no second registration), or only when the knob is on?
3. Exemplar pages only (at most 10, about 1.5 MB), or every content page (about 100 for template A, about 13 MB and
   2-3 more minutes of registration) for a closer match per page?
4. Are masked layouts and the template's own pictures acceptable in prompts for decks of the same owner? (No text
   leaves the registration; pictures and logo marks inside the body do.)
5. Swap the judge anchors when a render page is an anchor (proposed), or exclude anchor pages from rendering during the
   measurement?
6. Later: should the design review also see the renders, so that it judges density against the template rather than
   in the abstract?
