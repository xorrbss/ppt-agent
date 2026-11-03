# SVG illustrations and deck icons drawn or chosen by the model: pilot (2026-11-03)

Offline pilots, run outside the generation pipeline with `claude-sonnet-5-5` (and
`claude-haiku-5-5` where noted) against the real `svg-static/v1` validator, the
icon flattener and the vendored SVG→PPTX converter. The question was whether
the model can supply the deck's pictures itself, with or without an image
provider, and replace the fixed 300-icon library. Every number below is a
measurement from these runs; quality judgements are a reviewer's visual
judgement on rendered sheets, not a metric.

## Decisions taken

1. **Icons are chosen, not drawn.** For each deck the model picks, per figure
   label, one Lucide icon (plus an optional small badge icon) from the full
   Lucide set; composition is deterministic. Lucide is vendored for the backend
   from `lucide-static` **1.67.0** (2,018 distinct shapes, brand icons
   excluded), independently of the frontend's `lucide-react` 0.447.0. The
   bundled 300-icon library stays only as the offline fallback.
2. **Illustrations are drawn by the model** as contract SVG, bound to the deck
   palette and a shared deck style guide, with no text. The scene quota
   (`MAX_SCENE_SHARE`) and the cover/section exclusion are to be replaced by a
   per-page visual plan.
3. **`stroke-linecap` / `stroke-linejoin` = `round` are admitted** by the SVG
   contract (see "Contract" below).
4. Image providers remain for photographic scenes only; the provider-generated
   PNG icon sprite sheets go away.

## 1. Scene illustrations (16 pictures)

8 business messages × 2 thinking settings, 512×460 box, three style palettes.

| setting | accepted | latency / picture | output tokens | cost / picture |
|---|---|---|---|---|
| `thinking: between_tools` | 8/8 | 11–15 s | 1.8k–2.6k | $0.019–0.028 |
| adaptive, effort medium | 7/8 (1 overflow) | 6–9 s | 0.9k–1.5k | $0.010–0.016 |

* No off-palette colour and no text in any picture; the validator caught the
  one overflow.
* Every accepted picture converted to native DrawingML: 20–67 shapes in one
  group, `ppt/media` empty, LibreOffice re-render visually identical.
* Weakness: pictures drew smaller than their box; people were occasionally
  awkward.

## 2. Extended illustration patterns (18 pictures, $0.27, 32 s wall)

Adaptive / medium, with a shared style guide (flat front view, faceless rounded
people, DARK outlines width 3 with round caps, one LIGHT background shape,
ACCENT only on the key element, fill 80–90 % of the canvas).

| pattern | result | cost / latency |
|---|---|---|
| cover + 4 scenes, independent | consistent people, monitors, stroke and background across the deck | $0.011–0.015, 6–8 s each |
| same, scenes given the cover SVG as reference | marginally more uniform, but scenes copy the cover's composition; adds a serial step | same, +6 s |
| people-heavy scenes (onboarding, safety check, call centre, workshop) | recognisable; multi-person overlap still awkward at times | $0.009–0.014, 5–9 s |
| As-Is / To-Be pair (one call, two SVGs) | same cast both sides; the change is clear for data integration, weak for "mobile approval" | $0.025, 13 s per pair |
| process spot set (4 small SVGs, one call) | shared scale and style, each step legible | $0.02–0.04, 10–18 s per set |
| pictograph (10 people, 3 highlighted) | built deterministically from a Lucide icon, exact counts | no model call |

Box fill (bounds area / box area) was 0.68–0.84 with the "fill 80–90 %"
instruction. Conclusion: the style guide alone is enough for consistency; no
reference chaining.

## 3. Deck icons (32 labels: a 16-label security deck and a 16-label automation deck)

Bundled library today (concept table → 300 composed Lucide icons):

* security deck: 6 distinct icons for 16 labels (the key icon 4×, shield 4×,
  firewall 4×), 2 labels without an icon;
* automation deck: 9 distinct icons, 4 labels without an icon.

| approach | accepted | distinct per deck | latency | cost / deck (16 labels) |
|---|---|---|---|---|
| model draws duotone icons, 8 per call | 32/32 | 16 | 13–14 s per call | ~$0.05 |
| model draws Lucide-style line icons, 8 per call | 32/32 | 16 | ~10 s per call | ~$0.03 |
| **model picks Lucide icons (Sonnet)** | 32/32 valid names | 16 | ~3 s | ~$0.02 (~$0.002 with prompt caching) |
| model picks Lucide icons (Haiku) | 32/32 valid names | 16 | 4–6 s | ~$0.0014 |

Drawn icons were legible at 28 px but less polished than Lucide; picking keeps
Lucide's designed geometry and fits each label (fingerprint for multi-factor
authentication, database + lock for encryption, siren for incident response,
piggy bank for cost saving, scale for governance). Badges were used on 6–7 of
32 labels.

Lucide coverage: the frontend pins `lucide-react` 0.447.0 (published
2024-10-01, part of the upstream initial commit `78e1006f2`, 2025-05-10, for UI
icons); it has 1,530 distinct shapes. `lucide-static` 1.67.0 has 2,296 files /
2,018 distinct shapes, 623 new names (e.g. `chart-radar`, `clipboard-signature`,
`banknote-check`, `cloud-backup`, `brick-wall-shield`, `building-complex`); the
18 removed names are brand logos.

## 4. Contract: round caps and joins

`svg-static/v1` was introduced in `5f2e837ea` (2026-09-17) as a conservative
allowlist; the design note (`svg-generation-mode-design-20260916.md` §8) defers
"stroke effects" until verified through conversion. No record gives a defect as
the reason for omitting `stroke-linecap`/`stroke-linejoin`. The converter
already maps them (`cap="rnd"`, `<a:round/>`), and the omission makes every
icon, the bundled library included, render with butt caps in decks. Code that
assumes butt/miter: the header and template bounds (miter reserve, still
conservative for round joins), the native table path reader (keeps rejecting
non-butt caps), and the icon flattener's dot-to-circle workaround.

## Prompts used

Illustration style guide (system prompt preamble):

```
- Flat 2D vector, front view, no perspective. No shadows except at most one soft ground ellipse.
- People: simple rounded figures. Circle head with no facial features, rounded torso, simple arms;
  no realistic skin or hair colours, use palette colours only. Same proportions in every picture.
- Objects: rounded rectangles (rx 6-10), circles and simple paths. Outlines in DARK, stroke-width 3,
  stroke-linecap="round" stroke-linejoin="round".
- Composition: one focal group, centred, filling 80-90% of the canvas width and height.
  At most one large soft background shape (circle or blob) in LIGHT behind the focal group.
- Colour roles: DARK outlines and key shapes; ACCENT only for the single most important element;
  other palette colours for fills.
```

Icon selection (system prompt, followed by every Lucide icon name):

```
For every label choose:
- "main": the icon that most specifically depicts the label's own idea (its head noun / action).
- "badge": optional small second icon drawn in the bottom-right corner, ONLY when the main icon
  alone would be ambiguous and the badge adds the distinguishing part. Prefer null.
Within one deck every label must end up with a different (main, badge) combination ...
Answer with JSON only: {"icons": [{"k": <number>, "main": "<name>", "badge": "<name>" | null}, ...]}
```

Composition: a lone main icon fills the 24-unit grid; with a badge the main is
scaled 0.78 at the origin and the badge 0.4 at (14.2, 14.2) with its stroke
widened to 3.9 so both read at the same weight.

## Open risks

* All pictures were generated in isolation; how the author places them next to
  text, and what that does to the existing quality gates, is measured only once
  the pipeline integration exists.
* Multi-person interaction scenes are the weakest category.
* The visual judgements above are one reviewer's; nothing here is a user study.
