"""The 2026-08-19 authoring prompt text, restored for the ``august-0819`` profile.

Reconstructed from ``git show
f994ba36bb3714106625282f2ada4ec97a3f6ebb:servers/fastapi/utils/llm_calls/author_slide_rules.py``
-- the last commit of 2026-08-19, the day of the reference deck. Both literals
below are that commit's text character for character, with exactly two
insertions, each required by a gate that did not exist in August:

* ``{semantic_roles}`` after the style brief. ``data-semantic-role`` is read by
  the semantic-role repair pass and the capacity gate, and a slide whose title
  owner is unmarked is blocked outright, so no profile may drop it.
* the NEVER-hide-overflowing-content paragraph after FIT IS CRITICAL.
  ``utils.llm_calls.author_slide_hidden_overflow`` rejects those declarations at
  authoring time, so a profile that did not state the rule would only author
  slides the author stage then throws away.

Everything the current brief adds and this one does not -- the content-box
arithmetic, the connector clearance, the radius family, the gradient paragraph,
the expanded Korean/CJK line, the shared universals block, the compressed SVG
contract -- is composition guidance rather than a contract, which is the whole
point of the profile.

Do not edit these strings. They are a parent baseline, not living prose: the
only reason they exist is to make "the August prompt" reproducible, and an edit
here silently invalidates every A/B run measured against them. Design:
``docs/authored-author-prompt-profiles-design-20260912.md``.
"""

from __future__ import annotations


#: ``author_slide_rules.DESIGN_SYSTEM_RULES`` as of 2026-08-19, plus the two
#: contract insertions named in this module's docstring.
AUGUST_DESIGN_SYSTEM_RULES = """\
DESIGN SYSTEM (obey on EVERY slide so the deck is cohesive):
- Brand primary colour: {primary}. Brand fonts: {fonts}. Deck language: {language}.
- Canvas: a single 16:9 slide that renders at EXACTLY 1280x720px. Inline <style> only, no
  external JS. Body 1280x720, margin 0, overflow hidden.
- Document head: emit at most `<meta charset="utf-8">`. Never emit viewport, refresh,
  CSP, or any other meta `name`/`http-equiv` attributes; render security headers are
  injected by the application after validation.
- SELF-CONTAINED FONT POLICY: Never emit a Google Fonts <link>, CSS @import, remote font URL,
  @font-face, or data-font. Use a logical family plus its deterministic local fallback:
  "Noto Sans KR"/"Pretendard" -> "Malgun Gothic"; "Noto Serif KR" -> "Batang";
  "Inter"/"Roboto"/"DM Sans" -> "Aptos"; "Source Serif 4" -> "Cambria";
  "IBM Plex Mono" -> "Consolas". Example:
  `font-family:"Noto Sans KR","Malgun Gothic",sans-serif`.
  These rules make HTML render offline and editable PPTX typefaces deterministic. They do NOT
  mean that fonts are embedded in the PPTX; font embedding is outside this authoring workflow.
{style_brief}
{semantic_roles}
- PPTX-NATIVE SAFETY (keeps exported objects editable):
  - Draw decorative ticks, underlines, badges, and divider lines as real <span>/<div> DOM
    elements, never ::before/::after pseudo-elements; painted pseudo-elements rasterize that subtree.
  - EXPLAIN THE SLIDE WITH STRUCTURE, NOT DECORATION: when the CONTENT states a process,
    a comparison, an architecture, a hierarchy, a schedule, or a set of figures, BUILD that
    relationship on the slide out of real DOM elements -- boxes, connector rules, lanes,
    columns, bars, rings -- carrying the CONTENT's own Korean labels and numbers. A page that
    restates its bullets as prose beside an ornamental picture has explained nothing. Prefer
    HTML+CSS (flex/grid, borders, gradients, box-shadow) for these: the converter maps DOM
    elements to native editable PowerPoint shapes, while an SVG is flattened to a bitmap.
    Tables, KPI figures, org charts, gantt bars and schedule rails, and simple 3-to-5 box
    process flows ALWAYS stay HTML+CSS DOM: a figure baked into a picture cannot be
    corrected, searched or re-used, so accuracy and editability outrank rendering ambition.
  - Do not use inline SVG for simple decoration; use HTML+CSS. Reserve SVG for geometry HTML
    genuinely cannot express -- curved or radial paths, polylines, arcs, wedges.
  - INLINE SVG CONTRACT (when an essential chart/diagram truly needs SVG):
    - The root may declare only `xmlns="http://www.w3.org/2000/svg"` (exact value, root only),
      plus `viewBox`, `width`, `height`, and `preserveAspectRatio`. Never emit `xmlns:xlink`.
    - Use only inert presentation elements: `svg`, `g`, `path`, `rect`, `circle`, `ellipse`,
      `line`, `polyline`, `polygon`, `text`, `tspan`, `defs`, `linearGradient`,
      `radialGradient`, `stop`, `clipPath`, and `mask`.
    - Geometry/paint attributes are limited to the element's ordinary coordinates, `d`,
      `points`, `transform`, `fill`, `stroke`, `stroke-width`, `fill-opacity`,
      `stroke-opacity`, `opacity`, `stroke-linecap`, `stroke-linejoin`, `stroke-dasharray`,
      `stroke-dashoffset`, `vector-effect`, `clip-path`, and `mask`; `transform` is available
      on every shape, not only on `g`. Text may also use `font-size`, `font-family`,
      `font-weight`, `font-style`, `letter-spacing`, `text-anchor`, `dominant-baseline`, and
      `writing-mode` for a vertical axis label; gradients/stops may use their
      coordinate/units/transform plus `offset`, `stop-color`, and `stop-opacity`.
    - Never emit `script`, `use`, `foreignObject`, SVG `image`, animation, event attributes,
      `href`, `xlink:href`, or any network/file/data/javascript URL. Paint references may only
      be local fragment ids such as `clip-path="url(#clip1)"` or `mask="url(#mask1)"`.
  - Do not use text-shadow, filter, backdrop-filter, or mix-blend-mode.
  - Use only one uniform pixel value for border-radius; no asymmetric or percentage radii.
  - Freely allowed: linear-gradient backgrounds, layered box-shadow, and raster images;
    the converter maps these natively.
- FORBIDDEN: default round bullet dots; identical heavy-shadow boxed cards on every item;
  clutter; lorem/placeholder; text overflowing/clipping the frame; low contrast; off-palette hues.
- FIT IS CRITICAL — the frame is EXACTLY 1280x720 and CLIPS (overflow:hidden): EVERY element
  must fit fully inside it with NO overflow, NO clipping, and NO overlap between blocks. This is
  the single most common failure — prioritise it over visual ambition. Use less/shorter text and
  generous spacing rather than cramming.
- NEVER hide overflowing content to make it "fit": `-webkit-line-clamp`, `line-clamp`,
  `text-overflow: ellipsis`, and `overflow`/`overflow-x`/`overflow-y: auto` or
  `scroll` are all forbidden and are rejected before rendering. If content does not fit, reduce
  density (shorter copy, fewer items) or re-lay out the slide instead — never truncate or make
  it scrollable.
{type_scale}
- Heading sizing: size headings to the ACTUAL text length so they stay within their area, and
  budget vertical space for the real number of wrapped lines.
- No fragile positioning: do NOT hard-pin a block to a fixed top/absolute offset that a longer
  heading could overlap. Prefer normal document flow (flex/grid column with gaps); reserve
  absolute positioning for decorative layers only, and keep clear gaps between text blocks.
- Korean/CJK: set `word-break: keep-all` AND `overflow-wrap: anywhere` on text containers so
  words are never split mid-word across lines.
- Boxes/cards/labels: each one's text must fit ENTIRELY inside it with padding — never let text
  touch or clip an edge. Size the box to its content (or shrink the content to the box).
{header_system}{page_number_system}
- All visible text in {language}.
Return ONLY the complete HTML document."""


#: ``author_slide_rules.DEFAULT_STYLE_BRIEF`` as of 2026-08-19, verbatim. It
#: still carries the universals (margins, the one-accent rule, the eyebrow
#: spec, the allowed-variation list) that the 2026-09-05 brief lifted out into
#: ``author_slide_brief.SHARED_UNIVERSALS``, which is why this profile needs no
#: separate universals block.
AUGUST_DEFAULT_STYLE_BRIEF = """\
- Fonts: use the offline-safe logical stack {font_stack}; headings 800-900 weight, body
  400-500. Do not add any external font resource.
- Palette: build everything from the brand primary ({primary}) + neutrals (ink #0F172A, muted
  #64748B, hairline #E2E8F0, surface #F8FAFC, white). The primary is the ONE accent; no other hues.
- Margins ~64px (except intentional full-bleed colour zones).
- Consistent eyebrow: UPPERCASE, letter-spaced 0.15em, ~13px, primary, with a short tick/line.
- Strong, confident type hierarchy; make the single most important element dominant.
- Allowed (vary per slide): full-bleed/partial colour-blocked zones, asymmetric grids, a left
  sidebar, oversized numerals, thin rules, subtle tints of the primary, and SVG only for essential
  charts/diagrams that cannot be expressed in HTML."""


__all__ = [
    "AUGUST_DEFAULT_STYLE_BRIEF",
    "AUGUST_DESIGN_SYSTEM_RULES",
]
