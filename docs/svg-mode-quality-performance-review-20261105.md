# SVG mode quality and performance review (2026-11-05)

Scored review of the default generation mode (`strategy=svg`, driver `svg-v1`)
at commit `696be2ac`. It is the evidence base for
`docs/svg-mode-improvement-plan-20261105.md`, which turns the findings into work
items. Read that plan to implement; read this document to see why each item
exists.

## 1. Method

- Fifteen parallel reviews, each scoped to one layer: pipeline critical path,
  author prompts, quality gate, fallback/recovery, render/measure, native
  charts/tables/diagrams, assets, PPTX/PDF export, common header and user
  templates, LLM call layer, frontend, code health, test health, docs evidence,
  and one live run.
- The reviews were read-only. No repository file was changed to produce the
  numbers.
- **Live run:** two 6-slide decks were generated with the real Anthropic
  provider (`claude-sonnet-5-5`) through the fake-LLM harness in real-provider
  mode (`servers/fastapi/scripts/fake_llm_e2e/`). The pricing catalog was
  `anthropic-55-20261102` from `docs/handoff-svg-quality-20261103.md`. Both
  decks were exported to PPTX. The two runs cost $2.69 together.
  - D1: "중소기업을 위한 생성형 AI 도입 전략: 현황, 기대 효과, 단계별 로드맵, 리스크 관리",
    style `exec-report`.
  - D2: "2026년 국내 전기차 시장 분석: 판매 추이, 점유율, 가격대, 전망" with numbers in
    the prompt, default style.
- **Caveat:** the VM is 4-core, and pytest and benchmarks ran in parallel with
  the live run (load average about 6). Local stages (render, export) may be
  inflated by a few seconds; provider latencies are not affected.
- **Labels:**
  - "measured": run or reproduced in this review.
  - "code": read from source with a `file:line`.
  - "inferred": derived, not measured.
- Line numbers are relative to `servers/fastapi/` unless stated otherwise.

## 2. Scorecard

| Axis | Score /100 | How it is formed |
|---|---:|---|
| **Document quality** | **58** | live run 62 × 0.4 + mean of layer scores 55 × 0.6 |
| **Performance (time and cost)** | **50** | live run 52 × 0.4 + mean of layer scores 48 × 0.6 |
| **Engineering health** | **30** | mean of code health 28, recovery maintainability 15, test health 48 |

| Layer | Quality | Performance | One-line reason |
|---|---:|---:|---|
| Live run (2 decks) | 62 | 52 | D1 72 / D2 52; 208–270 s and $1.26–1.43 per 6 slides; 0 cached tokens |
| Pipeline critical path | – | 50 | 73–76 % of wall time passes before the first body page is authored |
| Author prompts | 49 | – | Rules are mostly prohibitions with no positive design system; 8.4k-token system prompt, uncached |
| Quality gate | 58 | 45 | Precise glyph-ink geometry, but clipping is classed as layout and contrast/proportion are unmeasured |
| Fallback pages | 35 | – | Title repeated and cut; placeholder is white; dark-style source fallback is unreadable |
| Recovery machinery | – | 15 (maintainability) | ~15k LOC, ~40 recovery revisions, incident-specific literals |
| Render and measure | 72 | 35 | Strict sanitizer; but a 10.4 MB font is base64-inlined into every render |
| Charts, tables, diagrams | 43 | – | Default decks get 0 native charts and 0 native tables |
| Icons and assets | 60 | 72 | Icon colour ignores the style accent in 42 of 44 styles |
| PPTX/PDF export | 64 | 55 | One text box per line; font instancing cache never reaches the worker |
| Common header and templates | 55 | 35 | Uniform header costs 77–99 s per deck; Pretendard-heading style always fails |
| LLM call layer | – | 45 cost / 40 latency | No prompt cache, no effort setting, one model for every stage |
| Frontend | 60 | 55 | Browser preview font differs from the server measurement font |
| Code health | – | – | 28: 68.8k LOC, 156 functions graded F, max CC 286 |
| Test health | – | – | 48: 187 of 4,903 svg tests fail; CI does not run pytest |
| Docs evidence | 66 | 48 | Honest measurement notes, but no like-for-like comparisons and almost no human ratings |

## 3. Live run

### 3.1 Per deck

| | D1 (`exec-report`) | D2 (default, data-heavy) |
|---|---:|---:|
| Generation wall time (job) | 266.9 s | 205.7 s |
| `export.pptx` (total / worker) | 27.6 s / 25.8 s | 25.7 s / 24.5 s |
| Cost | $1.427 | $1.262 |
| Logical / physical provider calls | 19 / 22 | 20 / 23 |
| Repair calls | 3 | 4 |
| HTTP 400 "compiled grammar is too large" | 3 | 3 |
| 429 / 5xx | 0 | 0 |
| Input / output tokens | 479k / 47k | 441k / 38k |
| Cached input tokens | **0** | **0** |
| Quality gate | passed 6/6 | review_required (4 passed, 2 flagged) |
| Text fallback pages | 0 | **2 (p1 cover, p5)** |
| Header candidate pages thrown away | 1 | 1 |

Page failures:
- D1 p3 first failed `body_intrusion`; D1 p5 first failed
  `required_text_missing("2024년")`. Both passed after repair.
- D2 p1 and p5 ended `author:attempts_exhausted` with
  `svg_common_header_body_intrusion`. Recovery was
  `blocked/validated_candidate_unavailable`.

### 3.2 Stage timings (seconds, D1 / D2)

```
content_brief          49.2 / 25.7
outline                26.5 / 21.4
prepare_local           2.4 /  1.4
infographic plan       16.0 / 15.9
icon selection          8.5 /  7.9
header design          41.8 / 29.9
header representative  49.1 / 40.7   (2 candidates authored and repaired, 1 discarded)
header selection        8.5 /  6.2
---- sequential setup 202 / 150 s = 76 % / 73 % of wall time ----
page fan-out (5 ∥)     ~36  / ~32
vision review (3 calls) 25.5 / 19.2
publish                 0.3 /  0.3
```

Cost split (D1 / D2):

| Stage | D1 | D2 |
|---|---:|---:|
| author | $0.43 | $0.41 |
| repair | $0.25 | $0.27 |
| **vision review** | **$0.42** | **$0.34** (27–29 %) |
| header | $0.10 | $0.09 |
| plan and icons | $0.11 | $0.07 |
| brief | $0.07 | $0.04 |
| outline | $0.06 | $0.05 |

Input tokens are about 67 % of cost.

### 3.3 Visual rating (1–5, measured by viewing the rendered PNGs)

| Page | Mean | Main defect |
|---|---:|---|
| D1 p1 cover | 3.8 | Common header also drawn on the cover, so the title appears twice |
| D1 p2 KPI | 3.7 | Empty top-right area; last line of a card about 5 px from its bottom edge |
| D1 p3 roadmap | 4.0 | Numbered list instead of a timeline |
| D1 p4 | 3.5 | 14 px text in 5 dense lines at the top, empty band at the bottom; the planned `hub_spoke` was not drawn |
| D1 p5 | 3.5 | Bottom 17 % empty; says "6가지" and covers 3 |
| D1 p6 | 3.7 | Two-column misalignment |
| D2 p1 cover (fallback) | 1.8 | Title cut to "…가…", same title shown three times, text dump |
| D2 p2 bars | 4.0 | Correct values and proportions |
| D2 p3 share | 4.2 | – |
| D2 p4 price bands | 4.2 | – |
| D2 p5 (fallback) | 2.3 | Title repeated, 45 % empty |
| D2 p6 outlook | 3.3 | **Bar proportions wrong** (see below); duplicates p2's chart |

**D2 p6 bar proportions:**
- Bars are 160/110/190/250 px for values 16.2/14.7/18.5/22.
- Correct heights would be 160/145/183/217 px, so a 9 % drop is drawn as a 31 % drop.
- Geometry checks, `required_text` and the vision review all passed this page.

Other observations:
- **PPTX editability:**
  - Neither deck contains a native chart, a native table or a picture. Icons
    are `custGeom` vectors and fonts are embedded.
  - Text boxes map 1:1 to SVG `<text>` (18–33 per page in D1). A multi-line
    paragraph becomes several `wrap="none"` boxes, so edited text does not
    reflow.
- **Korean text:**
  - Hanja "소규模" leaked into the D1 cover source and the speaker notes
    (`notesSlide1.xml`).
  - Sentence endings (~다 / ~습니다) are mixed within a slide.
  - There were 0 off-canvas texts and 0 overlaps.

## 4. Findings by layer

Ordered within each layer by impact. Items marked P0/P1/P2 are carried into the
improvement plan under the same identifiers.

### 4.1 Pipeline critical path

Small decks are slow because of a fixed, sequential, deck-level chain, not
because of page authoring.

1. The uniform common header is the largest single stage: 77–99 s, about 28 %
   of cost.
   - Body pages are dispatched only after it finishes
     (`services/generation_core/drivers/svg/collect.py:525-530` → `:697`).
   - It authors two full representative pages and keeps at most one
     (`services/svg_common_header_generation.py:653-657`).
2. The content brief runs before the outline with no output cap and a 600–900 s
   deadline. It takes 26–49 s.
3. The infographic plan and icon selection run serially after the outline
   (16 s + 8 s), because the header design input includes the representative
   page manifest (`services/svg_common_header_generation.py:227-230`).
4. Author concurrency is 5 (`services/generation_core/runtime/profile.py:54`),
   so a 7–10 slide deck authors in two waves. The overload cooldown sleeps
   while holding an author permit (`drivers/svg/author.py:541` inside the slot
   at `collect.py:1460`).
5. Tail budgets are oversized:
   - author wall timeout 893 s, which assumes 50–60 tok/s; about 170 tok/s
     was measured;
   - brief 600 s plus a retry;
   - plan and icon selection have no explicit timeout.
6. Six slides and above get local vision-review groups plus a serial global
   review (`drivers/svg/quality.py:444`).
7. The header time is booked under the receipt's `outline` stage
   (`collect.py:527→543`), which hides it in timing reports.

### 4.2 LLM call layer

1. **No Anthropic prompt caching on author, repair or vision calls.**
   - `cache_system_prompt()` exists (`utils/anthropic_structured_output.py:518`)
     but only `utils/llm_calls/select_deck_icons.py:107` uses it.
   - The deck prompt cache key is OpenAI/Codex only.
   - The author system prompt also cannot be cached as built: the
     correction/visual-intent rules are inserted in the middle of the system
     rules (`generation_core/authoring/author_slide_svg.py:1176-1203`), so the
     design block falls out of the stable prefix.
2. **Cache accounting ignores Anthropic usage.** Cached tokens are not read from
   `cache_read_input_tokens`, and the pricing model has no cache-write
   surcharge. Any cache change is unmeasurable until this is fixed.
3. **No effort or output cap on deck-level calls.** `get_extra_body()` returns
   `None` for Anthropic (`utils/llm_config.py:474-513`), so brief, outline,
   plan, icons and header calls run at the model default with a 32768 token
   cap (`utils/anthropic_structured_output.py:469`).
4. **One model for every stage.** Classification-style calls (icon selection,
   header titles, infographic plan) cannot be routed to a cheaper model.
5. **Vision review pays for a 400 on every call.**
   - The schema is rejected by the grammar compiler; the rejection is not
     remembered (`utils/anthropic_structured_output.py:697-718`). Every vision
     call costs one extra round trip: 6 of 6 calls in the live run.
   - The schema (about 29k characters) is also pasted into the system prompt
     in the prompt-JSON fallback
     (`generation_core/quality/vision_advisory_prompt.py:1158`).
6. **429 handling:**
   - a fixed 30 s cooldown that ignores `Retry-After`
     (`services/provider_overload.py:519`);
   - no deck-shared cooldown, so five pages back off and retry together.
7. **Repairs re-emit the whole SVG**, about 25 % of output tokens.

### 4.3 Author prompts (code + captured prompts)

The default snapshot (profile v8, quality-first v2, readability v2, new-copy
path) was captured through `pin_svg_style` with the provider stubbed.

1. **No positive design system.**
   - The rules define no type scale, no grid and no "information type →
     visual form" menu.
   - The only concrete style brief is explicitly demoted to optional
     (`generation_core/drivers/svg/art_direction.py:173-176`,
     `author_slide_svg.py:789-793`). Elsewhere the same file asks the model to
     keep its layout advice (`art_direction.py:190-198`), which contradicts
     the demotion.
   - Eight style YAMLs, including `default`, declare the same content
     archetype "two-column explanation" (`authored_styles/default.yaml:57`).
2. **Rule bloat and stacked revisions.**
   - The system prompt is about 8.4k tokens: rules about 5.75k (21.8k
     characters) plus design JSON about 2.65k.
   - About 750 tokens of the design JSON are operational metadata the model
     cannot use (`quality_recovery_plan`, header design plan seeds and
     digests).
   - "preserve/retain" appears 31 times and "never/do not" 77 times.
   - Benchmark-specific rules (classification lists, Gantt, clustered bar
     axis contract, about 430 tokens) ride on every page.
   - The closing new-copy paragraph (`author_slide_svg.py:1210-1229`) reverses
     earlier source-preservation rules.
3. **The outline produces bullet walls.**
   - It asks for 4–6 complete sentences per slide, about 500 characters
     (`utils/llm_calls/generate_presentation_outlines.py:85-117`).
   - It asks for no key message, no visual form, and no table or chart
     syntax, so the native-data detector (`native_requirements.py:15`)
     almost never fires.
   - The sequence regex (`visual_intent.py:61`) misses "1단계:".
4. **No Korean width guidance.**
   - The server fallback uses a 1 em Hangul width model
     (`drivers/svg/fallback.py:66`), but the prompt gives the model nothing
     equivalent.
   - Overflow and overlap are the main cause of first-attempt failures (21 %
     in earlier docs).
5. **Chart instructions contradict each other.** Curves are forbidden
   (`author_slide_svg.py:58`) while pie and donut charts are admitted
   (`native_declarations.py:56-62`).
6. **Density rules conflict.**
   - Deleting content, shrinking text and splitting the page are all
     forbidden, and the outline density cap is off by default.
   - The prompt asks for a "readability failure" report although the output
     is SVG only (`services/document_canvas_generation.py:141-142`).
7. Paragraphs may be written as one `<text>` per line, which later becomes one
   PPTX text box per line.

### 4.4 Quality gate

- **What works:** glyph-ink geometry on a real Chromium render catches text
  collisions, clipping, off-canvas text, font floor and required numbers.
- **Problems:**
  1. **`text_clipped` is a layout kind** (`generation_core/quality/page_failure_kind.py:64`).
     A page whose only defect is clipped glyphs is kept and flagged, so clipped
     text is published. `text_off_canvas` is a content kind (`:49`), so the two
     visibility defects are judged differently.
  2. **Measurement and infrastructure failures are content kinds**
     (`measurement_incomplete`, `validation_failed`, `:54-58`). They send a
     designed page to the text fallback without re-measuring.
  3. **Geometry-failed pages skip the vision review and redesign**
     (`drivers/svg/quality.py:426-432`). The worst pages get the fewest
     recovery chances (one paid correction).
  4. **The reviewer is weak.** It is the author model at low reasoning with a
     single verdict sample (`quality/vision_advisory.py:351`,
     `vision_advisory_prompt.py:922`). A recorded case (2026-10-17) judged the
     same page passed at source review and `empty_band` at final review.
  5. **Not measured:**
     - contrast;
     - text overflowing its card (only detected when a later opaque `rect`
       covers it, `source_paint_occlusion.py:118-129`);
     - shape–shape overlap and alignment grid;
     - cross-page consistency;
     - chart value-to-geometry proportion (see D2 p6).
     The connector-direction check is off by default (`drivers/svg/render.py:803`).
  6. **Hard-coded thresholds:** 0.5 px, 9.0/9.5 pt, text-area ratio 0.5/0.65.
  7. **Corrupted correction hint.** The failure-list JSON is cut at 3,000
     characters, which can hand the model broken JSON, and the generic branch
     dumps digests (`drivers/svg/render.py:962-971`).
  8. **Cost is not instrumented.** The vision-review budget is open to 18 calls
     for 6 pages. Nobody records review cost, redesign adoption rate or
     per-axis failure frequency.

### 4.5 Fallback pages and recovery

1. **Fallback page quality.**
   - **Ordinary fallback.** `fallback_page_svg` already uses the style's
     background, palette and fonts, with a cover-shaped body for the cover
     role (`drivers/svg/fallback.py:468-534`, `:635-667`). But in the live
     run (D2 p1, p5):
     - the title is repeated in the header and the body;
     - the 44 px cover title is cut with an ellipsis;
     - the body is plain bullets in the top-left with about 60 % of the
       canvas empty.
   - **Last-resort placeholder.** Fixed white background (`fallback.py:702`)
     and `_BODY_COLOR` text, ignoring the style. It is reached when the full
     fallback raises `ValueError` (`terminal.py:231`). It is the most
     plausible source of the "white cover in a navy deck" in the handoff
     (inferred).
   - **Source-reflow fallback.** Fixed text colour `#172033`
     (`drivers/svg/source_fallback.py:53`) on the style background: contrast
     1.02:1 on `strategic-navy` (measured). About 13 of 44 styles have dark
     backgrounds.
   - **Font floor.** Fallback body text can shrink to 4 px (`fallback.py:49`).
   - **v3 template frames have no fallback** (`terminal.py:302-305`): one
     exhausted page fails the deck.
2. **Fallback is reached after 2–3 paid author calls per page.** The main causes
   are contract violations that can be fixed deterministically:
   - `svg_contract_explicit_text_attributes_required` (12 of 30 pages in one
     record);
   - font weight 700 on templates;
   - duplicate ids.
3. **Recovery machinery.**
   - **Size:** 12 files, about 15.2k LOC, about 40 recovery revision strings
     (`svg_partial_recovery.py` 5,264 lines with a 458-line `build_manifest`,
     `svg_timeout_repurchase.py` 2,253 lines).
   - **Incident-specific literals in production modules:**
     - 18 operation UUIDs, `total_slides != 12`, model `gpt-6.1-sol`
       (`svg_timeout_repurchase.py:115-119, :254-267, :1099-1107`);
     - slide-index branches inside the general attempt-limit function
       (`svg_presentation_checkpoint.py:255` `index == 4`, `:275`
       `index in {2, 3}`).
     The slide-index branches run only when the manifest carries
     `bounded_completion_recovery` (`:250`), so they do not change ordinary
     decks. They are a maintenance hazard, not a correctness bug.
   - **Dead in practice:**
     - `svg_cached_author_render_recovery.py` pins converter digest `52b0a0…`
       (`:52`) against the current `aa9218bc…`, so it rejects every request
       (`:817-818`);
     - the zero-dispatch repurchase path requires profile v7 while the current
       profile is v8.
   - **Risk (inferred):**
     - partial recovery seals payload storage and then rejects non-PostgreSQL
       databases (`svg_partial_recovery.py:1563-1566`, `:4207`);
     - its prepared-episode cache is a process-local 128-entry LRU (`:47`,
       `:86-90`) checked without a cold-path fallback (`:5132`).

### 4.6 Render and measure (measured on this VM)

| Measurement | Value |
|---|---|
| `render_svg_with_metrics`, warm, default font embedded | 3.69 s mean (3.23–5.1) |
| Same SVG with a system font (no embed) | 2.31 s |
| Wrapper HTML size | 13.9–14.0 MB (Noto Sans KR variable 10.4 MB base64 + Pretendard) |
| CDP `Page.setDocumentContent` per page | 2.1 s (0.49 s without the embed) |
| Event-loop stall during one render | 0.95–1.3 s (2.3 s with 4 concurrent) |
| 4 pages sequential → 4 concurrent | 12.5 s → 8.8 s (concurrency barely helps) |
| Python peak memory per render | 122 MB (44 MB without the embed) |
| fontTools subset of one slide (94 characters) | 415 ms, 10.4 MB → 36 KB |

1. The default font is base64-inlined in full on every render
   (`services/svg_default_fonts.py:90-101`). The parent re-materializes it
   only to check a digest (`utils/slide_capture.py:227-237`,
   `slide_capture_worker_client.py:143`).
2. Synchronous CPU work runs on the event loop:
   - PIL decode and re-encode (`slide_capture_reusable_render.py:151-153, 226-227`);
   - 874 KB metrics `json.loads(json.dumps())` (`services/svg_renderer.py:578`);
   - the cache-budget size check runs 15 dumps;
   - the checkpoint compare and fsync (`svg_presentation_checkpoint.py:783-791`);
   - the PNG `atomic_write` (`drivers/svg/render.py:1005`).
3. `_render_cache_identity` runs 10 times per render over the 14 MB HTML
   (`utils/slide_capture.py:128-151`), about 0.67 s.
4. The production default spawns a worker process and Chromium per render
   (`slide_capture_worker_client.py:169`). The framed session protocol is off
   (`get_env_programme.py:329`), and the template and raster paths bypass the
   lease.
5. Renumbering re-renders every slide sequentially inside a DB session
   (`services/svg_document_mutations.py:437-505`), about 74 s for 20 slides.
   It also re-measures without the generation-time `readability_policy`
   (inferred).
6. Glyphs missing from Noto (✔ ★ ① 🚀) fall back silently and are not measured
   (`font_loaded` checks the primary family only, `svg_renderer.py:629`).
   - The template path's symbol fallback reads Segoe UI Symbol from
     `C:\Windows\Fonts` only (`svg_template_fonts.py:57-82`).
7. The glyph-clipping probe is skipped above 512 KB of HTML
   (`slide_capture_glyph_probe.py:146`). With the font embed every page is
   above the limit (inferred).
8. **Sanitization is strong:**
   - allowlist elements and attributes;
   - no `<!`/`<?`;
   - translate-only transforms;
   - `href` limited to slot ids or PIL-validated PNG/JPEG
     (`utils/svg_validation.py:54-67, 124-130, 222-238`).

### 4.7 Charts, tables, diagrams

1. **Native data needs a literal.**
   - Requirements come only from literal `chart:` lines and Markdown tables
     (`native_requirements.py:15, 44-74, 136-174`).
   - The outline is told to write them only when `CONTENT_STRUCTURE_POLICY_SCOPE`
     is on (`utils/content_structure_policy.py:33`, default `off`).
   - Result: default decks have 0 native charts and 0 native tables.
2. **The infographic plan is discarded for svg.**
   - Only node labels reach the asset offer
     (`generation_core/assets/offer.py:334-335`), and only `"icon: {label}"`
     slot text reaches the author (`asset_slots.py:126,147`).
   - Archetype, takeaway, weight, emphasis and density are dropped.
   - `archetype` does not occur in `drivers/svg/` or `author_slide_svg.py`.
   - The plan vocabulary has no `chart` or `table`
     (`models/authored_infographic_plan.py:44-66`).
3. **Value-label units are lost in PPTX.** `_source_data_label_format` accepts
   only bare numbers (`drivers/svg/export_chart_style.py:608-631`). "30%" or
   "1,200억원" produce no number format, so the editable chart shows "30".
4. **Chart types are limited.** The svg path allows clustered bar/column, line,
   area, pie and donut only (`native_chart.py:57-62`). The vendored converter
   already supports stacked, percent-stacked, scatter, waterfall and combo.
5. **Table + chart pages rarely pass:** 1 of 9 pages
   (`docs/content-structure-policy-20261104.md:221-228`).
6. **Diagram defaults point the wrong way.** The default rule forbids arrows
   between items (`author_slide_svg.py:78-79`). The connector rule that would
   allow sourced sequences sits behind the same off switch
   (`content_structure_policy.py:67-72`).
7. **Palette fallback is hard-coded.** Area charts and charts whose colour
   extraction fails use a hard-coded palette (`export_projection.py:29,130`),
   and the fixed axis format is `General` (`:190`).

### 4.8 Icons and assets

1. **Icon colour is wrong.**
   - Icons are painted with `brand.primary` or `#1E5EFF`
     (`generation_core/assets/offer.py:352, 384`), not the style accent that
     the author (`author_slide_design_system.py:25-43`) and vector drawings
     (`vector_offer.py:65-75`) use. 42 of 44 styles have a different accent.
   - Phosphor regular/fill/duotone icons always come out `#1E5EFF`:
     `_ink()` looks only for `stroke="#hex"` (`asset_slots.py:55-57`), and
     those families use `fill=` (`icon_pack/families.py:123-124`).
2. **Badge stroke mismatch.** Badge strokes are about 54 % of the main icon
   stroke (measured 3.06 vs 5.69). The pilot doc says they are widened; the
   code does not do it (`families.py:107-110`).
3. **Icon selection cannot abstain.**
   - The prompt forces one icon per label
     (`utils/llm_calls/select_deck_icons.py:43-60`).
   - Omitting an icon fails non-new-copy pages with `asset_icon_omitted`
     (`embed_assets.py:297-317`).
4. **Icon families mix within a deck.** Selection failure, more than 120 labels
   or a missing label falls back to the Lucide-based library
   (`offer.py:233-240`). Same-page duplicate icons collide on slot id
   (`asset_slots.py:144`).
5. **The ~80 % coverage figure is optimistic.** It was measured on the corpus the
   concept table was tuned on (re-measured 223/278). A fresh 40-label Korean set
   matched 82 %, with visible mis-matches.
6. **Stale cache shadows vector icons.** A stale authored PNG sprite cache entry
   shadows the vector library icon (`icon_pack/service.py:192-193`), and the
   label is then dropped.
7. **Performance and safety.**
   - Icon selection has no timeout (`select_deck_icons.py:108-119`).
   - Its system prompt lists the full family vocabulary (Tabler about 65k
     characters).
   - Vector-mode flattening and rasterization run synchronously (about 790 ms
     for a 150-shape drawing).
   - Up to 12 drawing calls are sent without a concurrency cap
     (`vector_offer.py:270`).
   - OCR text checks run only for cutouts (`scene.py:119`).

### 4.9 PPTX/PDF export

- **Measured (5 slides):** about 10 s without font embedding, about 41 s with
  it. Embedding is the PPTX default (`services/svg_export_operation.py:1061`).
- **No LLM call at export:** visual review reuses the generation-time advisory.

1. **The variable-font instancing cache never reaches the isolated worker.**
   - The cache root is `APP_DATA_DIRECTORY`
     (`services/svg_pptx_font_subset.py:146-153`).
   - The worker environment allowlist (`services/isolated_worker_process.py:46-95`)
     does not pass it.
   - So every export pays 11–24 s cold instancing; a cache hit is 1.2 s.
2. **One text box per line.** Paragraph merging is enabled only for raster
   redesign (`workers/public_svg_pptx_convert.py:116`
   `merge_paragraphs=scene.redesign_active`), so ordinary decks get one
   `wrap="none"` text box per line.
3. **One native fallback fails the asset-bearing (v2) export.**
   - `svg_generic_native_coverage_fallback`
     (`workers/public_svg_pptx_convert.py:181-183`) fails the whole export.
   - This contradicts `export_native_wire.py:8-10` ("The export does not
     fail").
4. **PPTX fidelity is unmeasured on Linux.** Provenance hard-codes
   `visual_quality_verified: False` (`svg_export_operation.py:1151`), so PPTX
   `release-v1` cannot pass on Linux/Docker. `soffice --headless` works on this
   VM.
5. **Converter reproducibility is broken.**
   - `chart-furniture.16` and `native-fixed-text.1` were edited into
     `vendor/slide_master` without patch files.
   - The worker says `CONVERTER_VERSION = native-fixed-text.2`
     (`workers/public_svg_pptx_convert.py:15`).
   - `test_converter_patch_rebuild_matches_shipped_bytes` fails.
   - Rebuilding the manifest changes `converter_digest()` (`:35-48`), which
     also hashes nine adapter files, and invalidates every stored export
     proof.
6. **Weights are collapsed.** Only regular and bold slots are embedded, so SVG
   500/600 render as 700. The subset baseline has no Hangul syllables, so
   newly typed Hangul falls back (inferred).

### 4.10 Common header and user templates

1. **Pretendard-heading styles always fail with the uniform header.**
   - The reduced header v1 uses `tokens.heading_font`
     (`services/svg_common_header.py:288`).
   - The space-advance table knows only the Noto Sans KR digest (`:25-29`),
     so `svg_common_header_font_material_invalid` is raised (`:158-166`).
   - `reduced_header_outcome` catches only three other errors
     (`svg_common_header_design_plan.py:213-214`).
   - Affected: `authored_styles/devax-si-proposal.yaml:29`. Reproduced
     offline.
2. **One provider error in the optional header stage fails the deck.**
   - `_paid_step` records `unknown` and re-raises
     (`svg_common_header_generation.py:293-297`).
   - Fallback is allowed only when all steps succeeded
     (`svg_common_header_design_plan.py:122`).
3. **The header stage is slow and sometimes wasted.**
   - It costs 77–99 s, serial.
   - One recorded run spent 43 s of design calls and still fell back to the
     reduced header (`docs/svg-generation-measurement-runbook-20261102.md:106-112`).
   - Worst case before body work: about 13–15 minutes (inferred).
4. **The Linux space-advance constant is wrong.**
   - The constant is 9.515625/34 em; Linux Chromium measures 7.95 px against
     9.52 px expected.
   - Four tests in `tests/unit/test_svg_common_header_space_advance.py` fail.
   - The error is on the safe side but wastes body space and misplaces
     centre/right alignment.
5. **Long titles are handled in opposite ways.**
   - The uniform header never shrinks: more than two lines is an overflow,
     and v1 drops to free above about 66 characters.
   - Template frames shrink each page independently to 60 % and then cut
     mid-word with "…" (`services/svg_templates/frame_composition.py:270-284`).
   - Neither receives a title width budget from the outline.
6. **The cover gets the header.** `common_header_cover_exception` defaults to
   false on the server (`models/generate_presentation_request.py:306`) and in
   the UI (`servers/nextjs/app/(presentation-generator)/services/api/presentation-generation.ts:433`), so the cover
   gets the header. This caused the duplicated cover title in D1 and the
   `body_intrusion` cover fallback in D2.
7. **Small intrusions are always repaired by the LLM.** `body_intrusion`
   repair is a full LLM re-author even for a few pixels
   (`drivers/svg/render.py:316-335`).
8. **User templates stay behind the flag** (`SVG_USER_TEMPLATES_ENABLED` off):
   - one public-deck generation measured gate 0/6 and fallback 5, with no
     re-measurement after fix 5;
   - the judge is calibrated on template A only;
   - `PATCH /{id}/draft` lacks the `public_error` mapping
     (`api/v1/ppt/endpoints/svg_templates.py:196`);
   - `list_templates` is N+1 (`services/svg_templates/catalog.py:116`).

### 4.11 Frontend (`servers/nextjs`)

1. **The preview font differs from the server font.**
   - The server measures and renders with bundled Pretendard/Noto Sans KR
     (`servers/fastapi/services/svg_renderer.py:38-70`).
   - The preview iframe has `font-src 'none'` and no font
     (`SvgSlidePreview.tsx:53,153`), so the viewer's OS font is used.
   - Slot-bearing slides switch between server PNG and live SVG on
     selection, changing typeface. The presenter flickers PNG→SVG on every
     slide, and `prefetchSvgSlidePreview` is never called.
2. **Template (fixed-glyph) decks re-request materialized SVG per slide** with
   no shared cache and `no-store` (`SvgSlidePreview.tsx:96-97,141`;
   `servers/fastapi/services/svg_asset_resolver.py:79-190`). A failure shows no
   PNG fallback and no retry.
3. **No virtualization.**
   - Thumbnails are not virtualized and iframes are `loading="eager"`.
   - `ViewportCulledSlide` never unmounts (`components/PresentationRender.tsx:70-87`).
   - The whole deck is cloned, serialized and SHA-256 hashed on every change
     (`presentationRenderGateway.ts:33-45`).
4. **No SVG text editing.**
   - Inline editing is off (`PresentationPage.tsx:71`), and the backend
     rejects SVG DOM edits (`api/v1/ppt/endpoints/slide.py:91`).
   - A typo needs a full page remake.
   - `SlideModel.is_authored()` returns false for svg (`models/sql/slide.py:107`).
5. **Smaller issues.**
   - The draft grid never starts polling after a first 404
     (`DraftCheckpointPreview.tsx:72,81-82`).
   - Tiles are fixed at 16:9 even for A4.
   - "New slide" opens `/upload` (`header/NewSlideButton.tsx:19`).
   - Hard-coded Korean strings bypass i18n.
6. **Security is good.** The server allowlist is backed by a `sandbox=""`
   iframe and CSP `default-src 'none'`. SVG never goes through
   `dangerouslySetInnerHTML`.

### 4.12 Code health (radon + AST, 187 modules, 68.8k LOC)

- **Size and complexity:**
  - 156 functions graded F, 32 with CC > 100.
  - The worst are `svg_partial_recovery.build_manifest` (CC 286, 458 lines)
    and `drivers/svg/render.py:482 _attempt_render` (CC 236, 585 lines, 50
    local imports).
  - `author_vision_qa.py:436` is the longest function at 842 lines.
- **Hidden import cycle.** 1,591 function-level imports hide an 81-module cycle
  (zero eager cycles). There are 113 upward layer dependencies, for example
  admission → recovery and quality → export.
- **Code-file digests act as authority.**
  - `svg_partial_recovery.py:144` hashes 62 files (about 2.2 MB) on each call.
  - `svg_cached_author_render_recovery.py:52` pins a converter digest.
  - `common_header_verifier_digest()` hashes 12 files per slide and feeds
    export identity.
  - Any refactor of these files invalidates stored evidence, and none of the
    hashes are cached.
- **Unregistered knobs.** About 30 knobs affect svg, and 17 of them are outside
  `constants/settings_registry.json`. `AUTHORED_*` names also change svg
  behaviour. The svg vision advisory is forced on regardless of env
  (`runtime/profile.py:136`).
- **Duplicated helpers.** `_digest` is re-implemented in 9 files with different
  serializer options (`allow_nan`), a latent byte-contract split.

### 4.13 Test health (4,903 svg-related unit tests in 185 files)

- **Results:** 4,665 passed, 187 failed (all reproducible, none flaky),
  51 skipped. About 6 minutes with 4 shards; line coverage 70 %.
- **Failures:**

| Class | Count | Examples |
|---|---:|---|
| Stale golden or byte baseline | 114 | `test_svg_design_16x9_baseline` (88, adapter `svg-style/v2`→v4 never re-captured), `test_svg_canvas_brief` (24), `test_canvas_admission_baseline` (1, 980 drifted rows) |
| Code violates a contract test | 2 | Korean literal, default canvas literal |
| Intentional change, expectation not updated | 22 | svg default strategy, sync svg 422, provider receipt fields |
| Test stub lags code | 22 | `SimpleNamespace` missing `kind`/`owner_id`/`id` |
| Environment-dependent, no skip guard | 25 | setsid 5, Segoe UI Symbol 7, pythoncom 8, Chromium font width 4, Office 1 |
| Suspected real regression | 2 | `test_svg_chart_fidelity` (0 `c:dLbls/c:showVal` in PPTX), `test_svg_page_quality[...][2]` (visual review fails only under policy v2) |

- **Product defect found through the tests:** `utils/slide_capture_pipe.py:60`
  uses `posix_spawn(setsid=True)`. uv's python-build-standalone lacks
  `POSIX_SPAWN_SETSID`, so svg PDF export and raster paths fail on a local
  Linux uv run.
- **CI gap:** CI (`bitbucket-pipelines.yml`) runs `ruff` but not pytest, which
  is how the goldens drifted across two adapter revisions unnoticed.
- **Coverage gaps:**
  - no HTTP-level end-to-end test (`/generate/async` → status → `export.pptx`);
  - no pixel golden;
  - recovery and template modules are 15–48 % covered.

### 4.14 Admission baseline drift (re-run for this review)

`tests/unit/test_canvas_admission_baseline.py` reports drifted rows, all from
rejected to admitted. The review first counted 51 from truncated test output;
the full re-capture (`scripts/canvas_admission_table.py --write`, item E2)
shows 980 changed rows of 2,646. The main kinds:

| Change | Before | Now |
|---|---|---|
| Raster reconstruction on 16:9 and 1:1 | `422 raster_a4_svg_required` | admitted |
| 1:1 topic/restyle generation | `422 authored_canvas_stage_disabled` / `authored_portrait_combination_not_supported` | admitted |
| Registered style + svg restyle | `422 svg_source_template_unsupported` | admitted |

- 1:1 matches the 2026-09-29 canvas-bench promotion
  (`docs/canvas-bench-square-ultrawide-20260929.md`).
- Raster reconstruction quality has only been measured on A4
  (`docs/screenshot-preserving-a4-svg-pilot-results-20260924.md`).

### 4.15 Evidence quality of the existing docs

- **No like-for-like timing comparison.** Model, header mode, style and slide
  count change between runs. Most of the per-page improvement from 45–55 s to
  25–45 s is the model change.
- **The flag-rate drop was a detector fix.** The fall from 0.27 to 0.019 came
  from fixing detector false positives, not from better generation.
- **Almost no human ratings.** The only human-calibrated score is judge v2 on
  template A (mean error 0.25). Every other design score is from an agent or an
  LLM judge, and the planned blind human rating never ran.
- **Gap to the source decks.** Template generation is judged 2.7–3.0 against
  about 4.4 for the source decks.
- **Evidence is not checked in.** The svg evidence folders the docs cite are
  not present under `artifacts/`.

## 5. What is already good

- **SVG contract and sanitizer:** a strict allowlist with fail-closed font
  resolution.
- **Native object pipeline:** declare → match the drawing → digest-bind →
  sidecar → project → read back. Fallback happens per object.
- **Terminal ladder:** flag-only, then content fallback, then placeholder, with
  unknown failure codes treated as content (conservative).
- **Icons:** native vector shapes, editable in PPTX. All 10,342 family icons fit
  the point budget.
- **Export:** identity reuse and single-flight. PDF is vector text with
  embedded fonts.
- **Measurement corpus:** the text-overlap detector corpus (25 Chrome-rendered
  cases) is a good pattern to extend.
