# Authored Generation Mode — build plan (single source of truth)

> For an autonomous **ultracode + /loop** session. Work top-to-bottom; each phase is
> verifiable. This is the agreed FUNDAMENTAL fix for "quality is still much lower than
> Genspark / Manus / Claude". Repo: `C:\project\PPT-agent\ppt-agent` (git, origin/main).

## STATUS — COMPLETE (2026-06-08, all 5 phases shipped & pushed to origin/main)

Authored mode is live as an opt-in `template:"authored"` (CLI `--mode authored`). The
default adaptive/template path is untouched (288 pytest tests pass). Commits:
`cbebe71a` P1 authoring core · `84fe77a7` P2 deck+image-PPTX · `7a5cf8d8` P3 vision-QA ·
`afb50ff8` P4 wiring (handler branch + service + CLI).

- **Open decision #1 (Anthropic key): RESOLVED → shipped codex-quality.** No Anthropic
  key is configured; the selected provider is **codex / gpt-5.5**. Authoring is
  provider-agnostic (one `client.generate` text call), so it runs on whatever provider
  is configured — add an Anthropic key + select `anthropic` to switch with zero code
  changes. Verified output on codex is frontier-grade (see below), so this was not a
  blocker.
- **Verified by rendering REAL decks** (not just unit tests): three coherent,
  consulting-grade decks — Korean "2026 생성형 AI" (6 slides), Korean "클라우드 전환" (4,
  through the real handler), English "Edge Computing" (6, real handler + vision-QA on).
  All open in PowerPoint (valid OOXML). Vision-QA caught injected overflow/placeholder/
  low-contrast flaws and re-authored them clean.
- **Bonus:** because assembly is pure-Python (python-pptx / PIL), authored export works
  on Windows even though the byte-PPTX converter runtime is Linux-only.
- **Files (all ≤500):** `utils/llm_calls/author_slide.py` (133),
  `utils/llm_calls/author_deck.py` (85), `utils/llm_calls/author_vision_qa.py` (94),
  `services/authored_presentation_service.py` (172), `utils/slide_capture.py` render
  helper (127). Handler gained only a thin routing branch.
- **Optional follow-ups (not blockers):** expose a brand/primary-colour + wordmark knob
  in the request/UI (today defaults to a brand blue + title-derived topic); granular
  per-shape PPTX editability (currently image-per-slide, user-accepted).

## Why (decision, settled)

- The quality gap is **paradigm**, not polish. Today: the model FILLS one of ~14 fixed
  React archetypes (variants only add a few more fixed options). Genspark/Manus/Claude
  let the **model AUTHOR each slide's layout** → unbounded design space → bespoke quality.
- The incremental work shipped this session (content density + clamp `1dd5eda9`; chart
  declutter `618bc2db`; 5-archetype variant system `6eed7a8d`/`b1452a4d`; vision-QA loop
  `334bfedd`→`ec7a4b47`) **raised the floor but is architecturally capped** — confirmed by
  the user testing it.
- **User constraint relaxation (pivotal):** editability can be best-effort — "save as PPTX,
  edit in PowerPoint" is fine; **document QUALITY is the priority.** This unlocks dropping
  the strict semantic-DOM/editable-PPTX constraints that capped quality.

## Proven (PoC — do NOT re-litigate, build on it)

1. **Single slide, codex:** model authored one bespoke HTML slide (sidebar + hero numeral +
   asymmetric layout) — dramatically better than the template for the same content. (Had a
   minor ghost-text overlap → exactly what the vision-QA loop catches.)
2. **6-slide deck, Claude (parallel, shared design brief):** cover (gradient panel + SVG
   digital-mesh) / editorial problem / numbered pillar grid / 4-phase timeline / hero-metric
   outcomes / bold closing — **coherent + consulting-grade, indistinguishable from frontier
   tools** — assembled into a real `authored_deck.pptx` (image-per-slide) that opens in
   PowerPoint. PoC artifacts were rendered under the OS temp dir; the reusable IP (design
   brief + authoring prompt + assembly) is captured below.

## Architecture: replace the COMPOSER, reuse everything else

```
outline + brief (REUSE)  ->  per-slide model AUTHORING (NEW: Claude writes bespoke HTML)
  ->  headless render (REUSE servers/fastapi/utils/slide_capture.py)
  ->  optional vision-QA self-correction (REUSE utils/llm_calls/vision_qa.py: critique -> re-author)
  ->  image-based PPTX (NEW small: python-pptx, one full-bleed picture per slide)
```

**Reusable assets (keep):** outline/brief (`utils/llm_calls/generate_presentation_outlines.py`,
`generate_content_brief.py`), `utils/slide_capture.py` (chrome subprocess + PIL crop, 1280x720),
`utils/llm_calls/vision_qa.py` + `critique_slide.py`, the 15-provider LLM + 9-provider image
abstractions, persistence, the CLI (`scripts/ppt-agent.mjs`), the theme-token system (brand
colours/fonts), the existing adaptive template path (keep as the FAST / default mode).

**Replace:** the composer — instead of `compose_slides` filling typed archetype slots, a new
authoring step where the model writes a self-contained 1280x720 HTML document per slide.

## Model

- **Quality requires Claude** (design-strong). The provider abstraction already supports
  `anthropic`; needs an Anthropic API key. **codex (gpt-5.2) works as a fallback but lower
  quality** (PoC #1). Make the authored-mode model configurable (default anthropic if a key is
  present, else codex). **OPEN: confirm an Anthropic key is available** (else ship codex-quality).
- The PoC authored with Claude via the workflow's agent model (the session model). In the
  backend, authoring is a normal `client.generate` multimodal-capable text call to the
  selected provider.

## Build phases (the loop drains these)

### Phase 1 — authoring core (`utils/llm_calls/author_slide.py`)
- `author_slide_html(slide_brief, design_system, brand, role, index, n) -> html`: one
  `client.generate` call returning a complete self-contained 1280x720 HTML doc. Strip code
  fences. Use the **design-system brief + authoring prompt verbatim from the appendix** below.
- Coherence across slides = the SAME `design_system` brief (brand palette + fonts + visual
  language + do/don't) passed to every slide, plus the slide's role + content.
- Verify: author 1 slide for sample content, render via chrome, eyeball.

### Phase 2 — deck authoring + render + PPTX (`utils/llm_calls/author_deck.py`)
- `author_deck(outline, brand/theme) -> List[html]`: author each outline slide concurrently
  (`asyncio.gather`), shared design brief; derive per-slide ROLE from position/content
  (cover / content / metrics / timeline / closing) — a light deck-plan.
- Render each via `slide_capture` primitives (or a small render-html-string helper) -> PNG.
- `build_image_pptx(images) -> pptx`: python-pptx, slide size 13.333"x7.5", one full-bleed
  picture per slide (see appendix). Perfect fidelity, opens in PowerPoint.
- Verify: produce a real 6-slide deck + open the PPTX; render a contact sheet.

### Phase 3 — vision-QA self-correction (REUSE)
- After rendering, run the existing vision-QA critique on each authored slide; for flagged
  slides (overflow/overlap/placeholder/contrast) re-author with the critique as feedback
  (bounded 1-2 cycles). Reuse `vision_qa.py`/`critique_slide.py`. Opt-in.

### Phase 4 — wire as an opt-in MODE (do not disturb the default path)
- A new generation mode: simplest is a request flag (e.g. `mode: "authored"` or
  `template: "authored"`) on `GeneratePresentationRequest`, routed in
  `generate_presentation_handler` to the authoring pipeline instead of `compose_and_project`.
  Default unchanged. Persist the authored deck (store slide images + the html). Export =
  image PPTX (+ optionally PDF). The CLI gets a `--mode authored` flag.
- Theme/brand: inject the selected theme's primary colour + fonts into the design brief.

### Phase 5 — verify + harden
- Real decks across topics; confirm coherence + the PPTX opens in PowerPoint; vision-QA
  catches injected flaws; the default (template) path is byte-identical (regression);
  CI green (pytest / Test All / G4). Keep files <=500 lines; no secrets in logs.

## Guardrails (this work)
KISS / YAGNI / files <=500 / no hacks / **opt-in so the default adaptive path is unaffected** /
verify by rendering REAL decks (not just unit tests) / never print secret values (booleans/model
names only) / Docker only via WSL `wsl -u root` / temp scripts removed after use / commit in
logical units with trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## Open decisions to surface (don't guess)
1. Anthropic API key for Claude authoring? (else codex-quality fallback.)
2. PPTX editability: image-per-slide (perfect fidelity, not granularly editable) — user already
   accepted this; revisit only if they want html->pptx shape mapping instead.
3. Keep the adaptive template path as the fast/cheap default mode? (assume yes.)

---

## Appendix A — design-system brief (the crux IP; reuse verbatim)

```
DESIGN SYSTEM (obey on EVERY slide so the deck is cohesive):
- Canvas: a single 16:9 slide that renders at EXACTLY 1280x720px. Inline <style> only, no
  external JS. Body 1280x720, margin 0, overflow hidden.
- Fonts: load the brand fonts (default Noto Sans KR for Korean) from Google Fonts; headings
  800-900 weight, body 400-500.
- Palette: build everything from the brand primary + neutrals (ink #0F172A, muted #64748B,
  hairline #E2E8F0, surface #F8FAFC, white). The primary is the ONE accent; no other hues.
- Margins ~64px (except intentional full-bleed colour zones).
- Consistent eyebrow: UPPERCASE, letter-spaced 0.15em, ~13px, primary, with a short tick/line.
- Consistent footer on content slides: small muted slide marker left + a small wordmark right.
- Strong, confident type hierarchy; make the single most important element dominant.
- Allowed (vary per slide): full-bleed/partial colour-blocked zones, asymmetric grids, a left
  sidebar, oversized numerals, thin rules, subtle tints of the primary, inline-SVG icons/charts.
- FORBIDDEN: default round bullet dots; identical heavy-shadow boxed cards on every item;
  clutter; lorem/placeholder; text overflowing/clipping the frame; low contrast; off-palette hues.
- All visible text in the deck language.
Return ONLY the complete HTML document.
```

Per-slide authoring prompt = "You are an elite presentation/brand designer. Author slide {i} of
{n}. Deck topic: {topic}. This slide's ROLE: {role}. CONTENT: {content}. {DESIGN SYSTEM}. Design
THIS slide bespoke and premium (McKinsey/Apple-keynote quality) within the shared system, vary
the layout to suit the role, output ONLY the complete self-contained HTML document."

Roles used in the PoC (derive similarly from the outline): COVER / PROBLEM (editorial) /
PILLARS (numbered modular grid) / ROADMAP (horizontal phased timeline) / OUTCOMES (one dominant
hero metric + supports) / CLOSING (bold statement + CTA).

## Appendix B — image PPTX assembly (python-pptx, verified working)

```python
from pptx import Presentation
from pptx.util import Inches
prs = Presentation()
prs.slide_width = Inches(13.333); prs.slide_height = Inches(7.5)   # 16:9
blank = prs.slide_layouts[6]
for img_path in slide_png_paths:                # 1280x720 renders
    s = prs.slides.add_slide(blank)
    s.shapes.add_picture(img_path, 0, 0, width=prs.slide_width, height=prs.slide_height)
prs.save(out_pptx)                              # opens in PowerPoint, full-bleed per slide
```

## Appendix C — parallel authoring via Workflow (optional, used in the PoC)
The PoC authored 6 slides in parallel with a Workflow (one designer agent per slide, agent
model = the session model = Claude). For the PRODUCT, authoring should be plain backend
`client.generate` calls (`asyncio.gather`) against the configured provider — no Workflow needed
at runtime. The Workflow is only useful for offline PoC/quality experiments.
```
