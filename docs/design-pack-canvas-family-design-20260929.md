# Design-pack canvas family: design (2026-09-29)

**Status 2026-09-29: deferred.** Design packs are declared 16:9-only (`design_pack_canvas_unsupported`), so this canvas-native family is not being built. The variant store, variant source drafting and manifest v2 that this design retires were removed in `docs/svg-master-no-stretch-and-variant-removal-design-20260929.md` section 2 (implemented); its S0 executor work and v3 manifest were not.


Status: design only. No code was changed. Supersedes the drafting half of
`docs/aspect-ratio-expansion-design-20260928.md` decision 9 / section 5 (per-canvas variants by one
deck-level re-layout, commit `17059ccb6`). The admission half of that decision (a pack serves another
canvas only through a separately approved package, never by shrink-to-fit) is kept.

User decision this design implements: **templates are rebuilt natively for each canvas, not re-laid
out.** Each canvas is a first-class pack compiled by the same compiler from its own canvas-native source.
The packs share a brand identity and a role-coverage contract. No geometry is derived from the 16:9
frames.

Evidence base: the code paths cited below (line numbers at `e8475ee6f`), the three trial packs and
their re-layout drafts in the session scratchpad (`design-pack-variants/store`, `previews/`), and
`draft_run.log`.

---

## 1. Current architecture (facts)

### 1.1 What a pack is

| Layer | Where | Content |
|---|---|---|
| Manifest | `design_pack/manifest.py:36-94` | `pack_id`, semver `package_version`, `source_kind` (`pptx`/`authored`), source and structure digests, an inventory of sha256-pinned files, and `package_digest` over all of it. v2 adds `canvas_id`, `variant_of` and `status` (`draft`/`approved`). A v1 manifest is treated as approved (`effective_status`, `:65-66`). `ingest_design_pack` still writes **v1** manifests (`ingestion.py:142-150`, no `schema_version` passed). |
| Structure | `design_pack/models.py:296-375` | One fixed `Canvas` (`:52-55`, px or EMU), ordered `frames`, a `DesignTokenSummary`, and structural representative renders. The v2 structure seals slots as executable only with a patch handle, capacity envelope, container and confidence of at least 0.95 (`:340-348`). |
| Frame | `models.py:253-279` | `role` ∈ {cover, section, content, data, timeline, architecture, process, closing}, `density`, compiler-derived `FrameCapabilities` (`:130-155`), editable `slots`, protected `chrome` (`NativeElement`, `:207-236`), and `editability`. |
| Slot | `models.py:168-204` | `semantic_role` (free string: title, subtitle, heading, supporting, body, visual, data), `accepted_kinds` (text/image/table/chart), absolute `bounds`, `text_style`, and a `CapacityEnvelope` (`:105-127`: max characters and lines, table rows and columns, numeric items, image aspect). |
| Source | inventory role `source` | For `authored` packs this is `authored-design-source/v1` JSON. **Each frame carries the full slide `html`** plus a Chromium `sourceRender` PNG and measured `elements` (built by `api/v1/ppt/endpoints/design_packs.py:227-298`). For `pptx` packs it is the PPTX. |

Compilers: `compile_pptx_design_source` (`compiler.py:895-1063`) reads slides, layouts and masters.
Capacity comes from source geometry (`_capacity`, `:476-529`; `_capacity_envelope`, `:532-588`).
`compile_authored_design_source` (`:1105-1392`) turns authored elements with `editable` and
`slotApproved` set into slots and everything else into chrome. Representative renders are
`structural_svg` slot outlines only (`structural_render.py:21-53`), with no colour, fonts or chrome.

Registration (`design_packs.py`):
- `/from-presentation` (`:323-377`) renders each authored slide in Chromium
  (`render_html_to_png_with_metrics`). It extracts `data-qid` elements, infers kind and role from the
  tag (`_kind_and_role`, `:115-138`: h1 → title, h2/h3 → heading, li → supporting, img → visual,
  table → data), approves visible text or media as slots (`:141-168`) and estimates capacity with a
  conservative 0.9 em per character (`:171-224`). **The canvas is hard-coded to 1280×720**: bounds
  are clamped at `:252-253` and the source canvas is set at `:359`.
- `/from-pptx` (`:380-415`) compiles a PPTX.

Observed on the three trial packs (all v1, `authored`, 1280×720, 10 frames taken from the first ten
slides of one real deck):
- Each frame has 22–109 text slots, almost all labelled `body`. There is one `title` slot in the whole
  devax pack.
- There are no table or chart slots, and `tokens.typography` and `tokens.colors` are empty (the
  from-presentation path emits no `textStyle`).
- One pack has no chrome at all.

In practice these packs are **deck snapshots, not templates with semantic slots**. That matters for
§2.2 and §7.

### 1.2 How generation consumes a pack

Everything below is authored strategy + `generation_mode="registered-template"` only. svg never admits
a pack (`docs/canvas-expansion-admission-spec-20260929.md` table).

1. **Route.** `resolve_generation_route` (`generation_route.py:130-200`) loads the pinned package and
   seals `{designPackId, version (major), digest}` into the route receipt. `load_pinned_design_pack`
   (`:86-104`) matches `pack_id` + major + `package_digest` over `registry.pinnable()`.
2. **Executable pack kind.** `run_source_native_generation` (`source_native_generation.py:3941`) reads
   the pack source through `_source_json` (`:268-281`), which **rejects non-authored packs**
   (`source_native_authored_pack_required`, `:273`). A PPTX pack registers but cannot generate.
3. **Two frame-binding lanes.**
   - *Page lane* (brief with numbered page markers, or a source-restyle manifest).
     `build_source_content_admission` (`source_unitization.py:405-470`) tries every frame whose
     `frame_text_capacity_contract` (`:248-269`) exposes a **required title-like slot** (frames without
     one are not candidates). It keeps the frame with the largest total capacity that unitizes the
     page. `build_source_verbatim_capacity_admission` (`source_verbatim_capacity.py:1434`) then seals
     per-page frame ids and a renderer geometry scale.
   - *Brief lane.* `_StructuredNarrativeProvider` (`source_native_generation.py:393-587`) gives the
     LLM `serverFrameAssignments`, one per frame. When the draft has exactly as many slides as the
     pack has frames, it copies role, density and `frameSemantics = source-frame-NNN` from frame *i*
     onto slide *i* (`:547-577`), so the deck replays the pack's frame sequence. Otherwise the frame
     search matches on the LLM's roles.
4. **Frame search.** `search_source_native_frames` (`narrative_ir/frame_search.py:299-364`) applies hard
   constraints: role, density, capacity against the slot envelopes (`_capacity`, `:136-230`),
   required-slot coverage and frame semantics. It then Pareto-ranks the survivors. `_pack_snapshot`
   (`source_native_generation.py:352-390`) projects the structure into that search (one role per
   frame, all data representations, slot contracts as envelopes).
5. **Filling.** `_SourceNativeExecutor.execute` (`:3339-`) takes each selected frame's **source `html`**
   (a missing one fails with `source_native_frame_payload_missing`, `:3403-3408`). It builds a
   composition plan (`composition.py:206`) and patches content into the slot qids
   (`_apply_composition_plan`), then captures in Chromium (`:3476-3477`) and runs quality observation.
   **The frame HTML is the render authority. Slot bounds are measurements of it, not inputs to it.**
6. **Canvas.** `SourceNativeCanvasSession.from_request` (`source_native_canvas.py:66-81`) builds a
   contain placement from the pack canvas to `request._document_canvas`. It returns `None` when that
   attribute is absent. `_document_canvas` is set only for A4 and for svg source restyle
   (`services/authored_canvas.py:123-146`). For preset canvases (1:1, 21:9, 9:16, 9:13) the executor
   therefore captures at the default 1280×720 (`capture_kwargs` is `{}`). Flow and component geometry
   also reads `_SLIDE_WIDTH_PX` and `_SLIDE_HEIGHT_PX` (`source_native_generation.py:96-97, 1551-1554,
   1660-1666, 1768`). **The source-native executor is not canvas-aware for preset canvases today.**
   This blocks any non-16:9 pack package, variant or family member. See stage S0.
7. **Minimum type.** Source-native body text is at least 12 pt physical
   (`SOURCE_NATIVE_MIN_BODY_FONT_PT`, `source_native_canvas.py:31`). Dense source restyle allows 7 css
   px (`:29`). Verbatim text may scale down to 0.70 (`composition_capacity.py:19`).

### 1.3 Pin, digest, admission

- A pin is `{designPackId, version: major int, digest: sha256 package digest}`
  (`generation_route.py:62-83`).
- The registry (`registry.py:164-261`) discovers bases at `<root>/<pack>/<semver>/manifest.json` and
  variants at `.../variants/<canvas>/manifest.json`. It checks directory identity (`:68-86`), inventory
  hashes, structure and source binding, and that the variant canvas equals the registry canvas exactly
  (`:89-94`). `pinnable()` returns the bases plus the approved variants (`:253-261`).
- Admission for a non-16:9 canvas:
  - **Presets**: `_admit_design_pack_variant` (`authored_canvas.py:320-344`) calls
    `design_pack_variant_for_canvas` (`design_pack/variants.py:55-90`). A base pin resolves to the
    approved variant for the canvas, and `apply_design_pack_variant_pin` (`:101-110`) **re-pins the
    request to the variant digest**, so every downstream receipt seals the variant.
  - **A4**: `_a4_design_pack_variant` + `evaluate_a4_request` (`document_canvas_capabilities.py:217-265`,
    route `design-pack-canvas-variant`) do the same.
  - With no approved variant, the request keeps its existing rejection.

### 1.4 The variant path today (`17059ccb6`)

- `variant_source.layout_brief` (`variant_source.py:54-87`) lists every slot and chrome element with
  px bounds and font size.
- `variant_store.request_relayout` (`variant_store.py:52-78`) makes **one** structured LLM call. It
  returns new bounds (and optional font sizes) for every element and may not add or drop any.
- `build_variant_source` (`variant_source.py:209-239`) copies styles, paint and slot contracts and
  requires exact element parity (`design_pack_variant_relayout_incomplete`, `:223`).
- `write_variant` compiles and stages a v2 `draft`; `approve_variant` flips the status and reseals
  (`variant_store.py:81-168`). The CLI is `scripts/design_pack_variants.py`.

Trial (3 packs × 4 canvases):
- 21:9 keeps the 16:9 cluster and centres it, leaving about 206 px (13% of safe width) empty inside
  the safe area on each side, on top of the 16:9 inter-column gaps.
- 1:1 squashes x, so the same 30 slots are compressed into 592 px.
- 2 of 12 drafts failed parity (`draft_run.log`).

**New finding: an approved variant cannot generate.** `build_variant_source` emits frames with only
`id`, `type`, `role` and `elements`. The frame `html` and `sourceRender` are dropped (confirmed in both
the scratch store and the e2e approved copy). `_SourceNativeExecutor.execute` would therefore fail
every slide with `source_native_frame_payload_missing`. The e2e script (`e2e_verify.py`) proved
admission and re-pinning only.

This is not a patchable omission. The authored frame's geometry is an *output* of its CSS (flow, flex,
padding). New absolute rects cannot be written back into flow HTML without rewriting the CSS, which
re-authors the slide. Re-layout is structurally the wrong operation for HTML-sourced packs.

### 1.5 Reuse vs retire

| Keep (reuse as is or generalise) | Retire |
|---|---|
| `compile_authored_design_source`, the `models.py` contracts, `seal_structure`, `seal_manifest`, v2 `canvas_id`/`status` fields, `canvases.py` | `variant_source.py` (re-layout brief, schema, source builder) |
| Registry package loading and identity checks. The variant-canvas exactness check becomes the member canvas check | `variant_store.request_relayout`, `draft_variant`, `_SYSTEM` prompt |
| Admission seams: `design_pack_variant_for_canvas` → `design_pack_member_for_canvas`, `apply_design_pack_variant_pin` → member re-pin, both call sites | `design_pack_variants.py draft` |
| Atomic staging write and approve-by-reseal (`variant_store.py:81-168`) | `variants/` directory layout (migrated, §2.6) |
| `_authored_frame` Chromium extraction (`design_packs.py:227-298`), parameterised by canvas | |
| `inspect_authored_dom` (`utils/authored_dom_quality.py:651`) for overflow, clipping and contrast | |
| `composition_clause` / `apply_canvas_tokens` / `classify_canvas_shape` (`utils/llm_calls/author_canvas_shape.py`) | |
| `outline_canvas_budget.budget_for_canvas` for capacity sanity | |
| Pooled bench rule (`scripts/canvas_bench_*.py`) | |

---

## 2. The family model

A **family** is one base pack version plus a sealed **identity**, a sealed **role-coverage contract**,
and at most one **member** per registered canvas. The base is the member for its own canvas. Every
other member is a full, independently compiled pack. Its source is canvas-native: a customer source
for that canvas, or a generated one. Frames, frame counts and slot rosters may all differ between
members. They share only what identity and the contract require.

### 2.1 Identity (`family/identity.json`)

Identity is what a brand manager would call "the look". It contains no coordinates: all placement
intent is edge-anchored.

```jsonc
{
  "schemaVersion": "design-pack-identity/v1",
  "anchor": {"packId": "...", "packageVersion": "1.0.0", "packageDigest": "sha256:..."},
  "palette": [ {"hex": "#173A63", "role": "text-primary", "areaShare": 0.21}, ... ],   // roles: background, surface, text-primary, text-muted, accent-1..3, rule
  "neutrals": ["#FFFFFF", "#F4F6F9", ...],
  "typography": {
    "families": [{"stack": "\"Noto Sans KR\",\"Malgun Gothic\",sans-serif", "use": ["title","body"]}],
    "ladderPt": {"title": 28, "heading": 18, "body": 13, "caption": 10},    // physical points, measured on the base
    "weights": {"title": 700, "body": 400}, "lineHeight": {"body": 1.45}
  },
  "components": {"cornerRadiusPx": 6, "ruleWidthPx": 1, "cardFill": "surface", "cardShadow": "none",
                 "bulletStyle": "square-accent", "numberBadge": "circle-accent-1"},
  "assets": [ {"assetId": "logo-primary", "sha256": "...", "mediaType": "image/png", "aspect": 3.2,
               "use": "logo"} ],
  "chrome": [ {"kind": "logo", "assetId": "logo-primary", "edge": "top-right", "insetOfSafeMargin": 0.5,
               "heightPt": 18, "on": ["content","data","process","timeline","architecture"]},
              {"kind": "page-number", "edge": "bottom-right", "on": ["*-cover"]},
              {"kind": "footer-text", "text": "…", "edge": "bottom-left", "on": ["content"]},
              {"kind": "title-rule", "under": "title", "color": "accent-1", "widthPx": 2} ],
  "imagery": {"tone": "flat line icons, no photos", "source": "reviewer"},
  "notes": "free text written or edited by the reviewer",
  "extraction": {"extractorVersion": "identity-extract/v1", "baseRendersDigest": "sha256:..."},
  "identityDigest": "sha256:..."        // canonical_digest over everything above
}
```

**How it is extracted.** Extraction is deterministic, one Chromium pass over the base.
- **Authored bases.** Re-render every frame `html` with `render_html_to_png_with_metrics` and
  `normalize_browser_metrics` (the same capture registration uses), then:
  - *Palette*: computed text colour and background colour, weighted by painted area and clustered at
    ΔE2000 ≤ 3.
  - *Typography*: font families and sizes weighted by text area. Ladder roles come from slot semantic
    role and, where those are missing (the trial packs), from size rank.
  - *Components*: border radius and width, and box-shadow mode.
  - *Assets*: every `<img>` source hashed.
  - *Chrome intents*: an element repeated on at least 50% of non-cover frames, with the same asset hash
    or text and position within 2% of the canvas, becomes a chrome intent. Its edge is the nearest
    canvas edge pair and its inset is expressed relative to the safe margin.
- **PPTX bases.** `ppt/theme/theme1.xml` gives the colour scheme and major/minor fonts. Master and
  layout pictures and `dt`/`ftr`/`sldNum` placeholders (already walked by `_chrome`,
  `compiler.py:726-740`) give the chrome intents.

`imagery.tone` and `notes` cannot be extracted reliably. The extractor leaves them empty and the
reviewer writes them in `family identity seal`. An optional LLM caption of three base renders may
propose them, marked `source: "llm-proposed"`.

**Storage and digest.** `<root>/<pack>/<ver>/family/identity.json` plus the referenced asset bytes
under `family/assets/`. It is sealed with `canonical_digest` (`base.py:20-28`) and immutable for that
family version: changing identity means a new pack version. The registry validates that
`anchor.packageDigest` equals the base package digest.

### 2.2 Role-coverage contract (`family/contract.json`)

The contract is derived from what generation actually needs (§1.2), not from the base frame list.

**G: global minimum for every member.** This applies to generated and customer members alike, but not
to the legacy base.

| Id | Requirement | Why (generation fact) |
|---|---|---|
| G1 | Frame roles `cover`, `content` and `closing` each have at least one executable frame | Brief-lane roles and frame search role matching (`frame_search.py:253-254`) |
| G2 | Every executable frame has exactly one `required` title-like text slot | Page-lane candidacy (`frame_text_capacity_contract`, `source_unitization.py:248-269`) |
| G3 | Content frames span capacity tiers: at least one frame with body capacity ≥ 2.2 × `target_chars[standard]`, and at least one with ≤ 1.0 × (per canvas, §4.1 S3) | The page lane picks the largest admitting frame, and the brief lane needs sparse and dense options |
| G4 | Content frames cover the three density labels the narrative uses (sparse, medium, dense), or the member records which are absent | `densities` hard constraint (`frame_search.py:257-258`) |
| G5 | Chrome intents in `identity.chrome` are realised on the frame roles they name | Identity conformance |

**P: base parity (derived from the base).** For every accepted kind (text, image, table, chart) and
every frame role present among the base's executable frames, the member has at least one executable
frame with that role and kind. Parity is on **roles and kinds, not frames**. A base with 10 frames can
have a 7-frame 1:1 member.

For the trial packs, P yields roles {cover, section, content, architecture, process, data, timeline,
closing} and kinds {text, image}. The base itself fails G2 on 9 of 10 frames. That is a pre-existing
page-lane limitation of those packs and is not gated here (open decision 5).

The contract is stored with its own digest. Members reference it by digest.

### 2.3 Member manifest (manifest v3)

`source-native-design-pack/v3` keeps every v2 field. It replaces `variant_of` with:

```jsonc
"memberOf": {"packId": "...", "packageVersion": "1.0.0", "identityDigest": "sha256:...", "contractDigest": "sha256:..."},
"canvasId": "ultrawide-21x9",
"sourceOrigin": "customer-presentation" | "customer-pptx" | "generated",
"status": "draft" | "approved" | "rejected",
"approval": {"reviewer": "...", "approvedAt": "...", "checkReportDigest": "sha256:...", "overrides": [{"check": "S1", "reason": "..."}]}
```

The inventory gains the file roles `provenance` (draft receipt: model id, prompt digests, identity and
contract digests, attempts and failures) and `check-report`. `PackageFile.role` is a Literal, so this
is a schema change (`manifest.py:28`).

Layout: `<root>/<pack>/<ver>/members/<canvas-id>/{manifest.json, structure.json, source/reference.json,
representative/, provenance.json, check-report.json}`.

An approved member is immutable. Re-drafting a canvas that has an approved member requires a new family
version, as `design_pack_variant_approved_exists` enforces today (`variant_store.py:89-92`).

### 2.4 Pin semantics and resolution

**Recommendation:** keep the wire format. **A pin names a family by its anchor (base) package digest.**
Admission resolves a member for the canvas and re-pins the request to that member's digest, exactly as
variants do today. A pin that names a member digest directly also resolves (replay stability).

Resolution of pin P for canvas X:
1. Find P among bases and approved members (`pinnable()`).
2. If P is a base: X equals the base canvas → the base; else the approved member of `(pack, ver)` for X →
   re-pin; else reject with a new explicit code `design_pack_canvas_unsupported`.
3. If P is an approved member: X equals its canvas → itself; else resolve via its `memberOf` anchor as
   in step 2.

Why a new family digest was not chosen: it would change whenever a member is approved, breaking every
stored pin. With anchor pins, approving a new member only turns a former rejection into an admission.
Outputs on existing canvases cannot change, because approved members are immutable. The route receipt
seals both `designPackPin` (member) and `familyAnchorDigest`. Nothing else in the generation chain
needs to know about families.

### 2.5 Registry

`DesignPackRegistry` gains `family(pack, ver)` (identity, contract, members) and
`members(pack, ver, include_drafts=False)`. `pinnable()` adds approved members. Loading validates
`memberOf` against the family files and the anchor digest, and checks the member canvas exactly against
`canvases.py` (reuse `_check_variant_canvas`). `list()`, `get()` and `latest()` stay base-only, as
today.

### 2.6 Migration

- **Existing bases.** Unchanged, with no re-sealing. `family init --pack --version` extracts identity
  and derives the contract. A base without `family/` serves only its own canvas, as today.
- **The `17059ccb6` variant store.** Retire it; do not keep it as a fallback. Reasons:
  - Variants are not executable (html dropped, §1.4).
  - The trial showed the quality ceiling.
  - No live store holds an approved variant (the doc records "no approved variants yet"; verify with a
    one-line registry scan per owner before deleting).

  Implementation:
  - Remove `draft` and the re-layout modules.
  - The registry skips `variants/` with a logged warning instead of raising, so stale drafts cannot brick
    a store.
  - `design_pack_family.py migrate-variants --delete-drafts` removes them after confirmation.
  - Rename the admission route id `design-pack-canvas-variant` to `design-pack-canvas-member`, and keep
    the old id as an alias for one release because it appears in A4 route policy wiring.

---

## 3. Native source generation (option 2) and customer sources (option 1)

### 3.1 Source priority per canvas

1. **Customer source for that canvas.**
   - (a) An authored presentation already at that canvas (for example an exact reconstruction or restyle
     deck made at A4 portrait), registered through a canvas-parameterised `/from-presentation`
     (`sourceOrigin: customer-presentation`). This is executable today once S0 lands.
   - (b) A PPTX of that slide size (`customer-pptx`). This compiles through `compile_pptx_design_source`
     but **cannot generate** (`source_native_authored_pack_required`). That gap is pre-existing and also
     applies to 16:9 PPTX packs, so the member is registrable but not approvable until a PPTX executor or
     PPTX-to-authored conversion exists (out of scope, §6 open decision 7).
   - Canvas matching for PPTX is exact EMU (`canvases.py:49-56`), and PowerPoint's A4 preset
     (10691813×7559675) does not match the registry's A4 (10692000×7560000). A 0.1% tolerance is needed
     when S3 reaches PPTX.
   - A "master set" is one PPTX per size, because a PPTX has one `sldSz`.
2. **Generated canvas-native source** (below).
3. **Otherwise unsupported** (`design_pack_canvas_unsupported`).

### 3.2 LLM task shape

This is not re-placement. The model designs a complete template for the target canvas.

**Inputs:**
- `identity.json` (and asset ids).
- `contract.json`.
- The target canvas: logical size, safe area, physical pt per px (`widthEmu / width / 12700`), and shape
  class from `classify_canvas_shape`.
- The shape-class composition rules (§3.3).
- The canvas content budget (`budget_for_canvas`).
- Two or three base renders labelled "style reference: palette, type, component treatment; **do not
  copy positions**" (open decision 2).

**Call 1 (deck plan, structured JSON).** The frame list: for each frame, a role, a composition archetype
from §3.3, a slot roster (semantic role, kind, target capacity as a fraction of the budget) and chrome
placements by identity intent. It is validated locally against the contract (G1–G5, P) before any HTML
is written, and retried up to two times with the violations fed back.

**Calls 2..N+1 (one per frame, parallel, HTML).** A self-contained HTML document at the canvas size.
Rules:
- A fixed-size body; `data-qid` on every element.
- Editable elements carry `data-slot-role` and `data-slot-kind`.
- Chrome carries `data-chrome=logo|footer|page-number|decoration|background`.
- Only identity font stacks, only palette colours, images only by `asset:<id>` (the builder inlines them).
- No script.
- Placeholder Korean text sized to each slot's target capacity.

The system prompt reuses the authored design-system rules adapted to the canvas
(`author_slide_design_system.py:140-141`: `apply_canvas_tokens` + `composition_clause`), so members
follow the same layout discipline as authored decks.

**Extraction.** A canvas-parameterised `_authored_frame`:
- `render_html_to_png_with_metrics(width_px, height_px)`.
- Clamp to the member canvas.
- Take roles from `data-slot-role` first and fall back to tag inference (`_kind_and_role`). Check whether
  the metrics collector already surfaces data attributes; if not, add them.

The output is `authored-design-source/v1` **with `html` and `sourceRender`**, compiled by
`compile_authored_design_source` and written as a `draft` member with `provenance.json`.

### 3.3 Composition rules per shape class

These extend `composition_clause` with archetypes per role. The deck plan must name one per frame.

| Shape (`classify_canvas_shape`) | Canvases | Archetypes and limits |
|---|---|---|
| baseline | wide-16x9 | The base itself; generation is not needed |
| wide-landscape | a4-landscape | Like 16:9, with two columns preferred over three. Tables ≤ 7 columns. Same margins and type, never smaller type |
| ultrawide | ultrawide-21x9 | Content split into 2–3 column **bands** across the full safe width, or a wide diagram with a text rail ≤ 560 px. Process and timeline as a horizontal 5–7 step band. Cover as a 40/60 split with a brand field. Data as a wide table plus a KPI rail. **No centred cluster with side gutters > 12% of safe width** (check S1) |
| square | square-1x1 | One focal column or a 2×2 grid, never three columns. Tables ≤ 4 columns. Comparisons as a pair or stacked. Title ≤ 2 lines. Page order: headline, figure, takeaway. Budget factor 0.51, so capacity tiers scale down |
| portrait | tall-9x16, tall-9x13, a4-portrait | Header band at the top, then a vertical stack. Timeline and process vertical. Comparisons stacked. Tables ≤ 4 columns. Captions below figures. Logo and footer intents mapped to the top and bottom bands |

### 3.4 Determinism and cost

Generation is not deterministic, and does not need to be: the member is sealed at approval, and runtime
determinism comes from the digest.

`provenance.json` records the model (`utils.llm_provider.get_model()`), prompt digests, identity and
contract digests, and every attempt. Re-running `member draft` produces a new draft, never an in-place
change.

**Cost per pack × canvas:** 1 plan call + N frame calls (N ≈ 7–12) + at most 2 regenerations per failing
frame, so about 10–30 calls, plus 3 Chromium renders per frame (extraction, 100% fill, 50% fill). This is
offline; no call is added to deck generation (the "no additional LLM call per slide" rule in
`aspect-ratio-expansion-design-20260928.md` is respected).

### 3.5 Failure handling

- Contract violations in the plan: retry the plan, at most twice.
- A frame that fails a hard check (§4) is regenerated with the measured failure (for example "slot
  `body-2` overflows by 3 lines at 100% fill") up to twice. After that the frame is dropped **if the
  contract is still satisfied without it**; otherwise the draft is written with status `draft` and a
  failing check report, and cannot be approved.
- There is no all-or-nothing parity, so one bad frame does not sink the member (unlike
  `design_pack_variant_relayout_incomplete`).
- Every failure code is recorded in provenance.

---

## 4. Quality gate

### 4.1 Automated checks

All checks are measured on Chromium renders of the member's own frame HTML at its canvas:
- `render_html_to_png_with_metrics(..., width_px, height_px)` + `normalize_browser_metrics` +
  `inspect_authored_dom`.
- **Fill levels.** "100% fill" sets every text slot to deterministic Korean filler of exactly
  `maxCharacters` (seeded from a fixed corpus), tables to `maxTableRows × maxTableColumns`, and images to
  a neutral placeholder at slot aspect. "50% fill" uses half of each.
- **Base references.** The same measurements run on the base once and are stored in the contract, so
  "relative to base" thresholds are reproducible.

**Hard checks** (block approval, no override):

| Id | Check | Threshold |
|---|---|---|
| H1 | Compiles: v2 structure seals (executable slots, confidence ≥ 0.95); structure canvas equals the registry canvas exactly | pass/fail |
| H2 | Role coverage: contract G1–G5 and P | all satisfied |
| H3 | Overflow at 100% fill: `inspect_authored_dom` overflow, clipping, `glyph_clipped`/`line_box_clipped`; element outside the canvas; slot outside the safe area (1 px tolerance); slot–slot overlap | 0 overflow; 0 outside; overlap ≤ 2% of the smaller box |
| H4 | Minimum type (computed size at 100% fill) | Body ≥ 12 pt physical (16 px on presets, 16.93 px on A4); title ≥ 1.4 × frame body; chrome text ≥ 9 pt. Shrink-to-fit scripts are disallowed |
| H5 | Identity conformance | Logo hash present on the frames `identity.chrome` names, at the named edge. At least 95% of text area in identity font stacks. Off-palette paint (ΔE2000 > 5 from palette ∪ neutrals, images excluded) ≤ 2% of painted area |
| H6 | Contrast (existing `_contrast_ratio`) | ≥ 4.5:1 body; ≥ 3:1 at ≥ 24 px |

**Soft checks** (block approval unless the reviewer overrides with a written reason, recorded in
`approval.overrides`):

| Id | Check | Threshold |
|---|---|---|
| S1 | Empty bands at 50% fill. Content region = safe area minus the title band. Project the union of visible content boxes (slots + non-background chrome) on the long axis (x for landscape shapes, y for portrait and square). Bands include the gaps to the safe-area edges | Largest band ≤ max(10%, base same-role value + 3 pp) of the safe extent, **and** total empty share ≤ base same-role value + 10 pp. Approximate values from the structural previews of frame 6: 16:9 base largest ≈ 9%, total ≈ 19%; 21:9 re-layout largest ≈ 13%, total ≈ 40%. **The total-share bound is what catches the trial failure**; the largest-band bound alone is marginal |
| S2 | Occupancy of content frames at 100% fill (content box union / safe area) | ∈ [0.45, 0.90] and ≥ base same-role value − 0.10. **Catches 1:1 cramming** at the top end |
| S3 | Capacity sanity against `budget_for_canvas(canvas).target_chars[standard]` (T). Body capacity C = sum of `maxCharacters` of non-title text slots | Across content frames: max C/T ≥ 2.2 (text-heavy ceiling 1100/500), min C/T ≤ 1.0, and median C/T within ±30% of the base's median C/T. Data frames: rows × columns ≥ base × budget factor, columns ≤ the shape limit (§3.3) |
| S4 | Type ladder | Title/body ratio within ±20% of identity; body px ≥ 0.9 × identity body at equal physical pt (no smaller type to fit more) |
| S5 | Chrome anchor precision | Inset within ±50% of the identity inset (normalised by safe margin) |

Thresholds are initial. They are calibrated by running the checks on the three trial bases and on the
re-layout drafts, which the checks must fail (S1 on 21:9, S2/S3 on 1:1). Calibration is part of S4
acceptance, and changing a threshold bumps the check report schema version.

S3 and the page lane: source-native generation does not read the outline budget. It is used because it
is the product's statement of how much content a slide on that canvas should hold (factor 0.51 on 1:1;
capped at 1.00 on 21:9, so 21:9 members get **width for composition, not for more text**).

### 4.2 Human approval (CLI first)

`scripts/design_pack_family.py`:
- `family init | identity show | identity seal | member draft --canvas X --source generate|presentation:<id>|pptx:<path>`
- `member check --canvas X` writes `check-report.json` and `review/index.html`.
- `member approve --canvas X --reviewer <name> [--override S1 --reason ...]` refuses on any hard
  failure.
- `member reject`, `family status`.

The review sheet (a local HTML file, publishable as an artifact on request) shows:
- A header with identity swatches (palette chips, a font sample at the ladder sizes, logo), contract
  coverage (role × kind matrix: base vs member), and the check table with measured value, threshold and
  verdict.
- One row per member frame:
  1. the base frame render of the same role at 16:9 (real `sourceRender`, not the slot outline);
  2. the member render with placeholder content;
  3. the member at 100% fill with overflow boxes in red;
  4. the member at 50% fill with the largest empty band shaded;
  5. a slot overlay coloured by semantic role.

`family status` prints pack × canvas: `approved` / `draft (failing: H3,S1)` / `unsupported`, plus the
bench stage (§4.3).

### 4.3 "As good as the base"

1. **Relative checks.** S2–S4 are already base-relative.
2. **Pooled bench.** Reuse `docs/canvas-bench-decision-rule-design-20260929.md` §4 unchanged in
   statistic (Newcombe hybrid on page flag rate, design effect ρ 0.15, tiers candidate/verified with
   margins 0.20/0.15). The pool key changes from `(style, strategy)` to
   `(familyAnchorDigest, strategy=authored, generation_mode=registered-template)`:
   - baseline = base member on 16:9;
   - candidate = member on X;
   - same manuscripts, interleaved.

   **Gaps:**
   - `canvas_bench.py run` has no design-pack pin or generation-mode argument (`:320-335`).
   - Whether the source-native lane publishes `quality_pages` (read by `canvas_bench_live.py:229`) is
     unverified; nothing in `source_native_generation.py` writes that key. Without it the primary metric
     is `unmeasured` and the rule returns `hold`.
3. **Blind pairwise rating.** Ten random frame pairs (base vs member, same role, placeholder content),
   recorded as informational only.

Recommendation: human approval plus all hard checks admits the member. Promotion to the UI picker
additionally requires the bench `candidate` tier (open decision 6).

---

## 5. Alternatives considered

| Alternative | Strengths | Why rejected | Where the chosen design is weaker |
|---|---|---|---|
| **A. Re-layout variants** (current `17059ccb6`) | One call; exact slot parity makes capacity comparable; no identity extraction; cheap review | Cannot add or remove elements, so 21:9 gutters and 1:1 cramming are built in. Parity failures (2/12). **Not executable**: geometry is an output of frame CSS, so moved rects cannot drive HTML (§1.4) | A is far cheaper and faster to add a canvas, and its members are recognisably "the same template". Family members may look like siblings rather than the same slide |
| **B. One responsive, constraint-based pack** (breakpoints or constraint layout; slidespec-style solver) | Single source of truth; a brand change propagates everywhere; new canvases come for free | Customer templates are fixed-geometry artefacts; neither customers nor LLMs author good responsive slide templates. Capacity envelopes become functions of canvas, so admission gets harder. The fork's own constraint repair pipeline applied 0 repairs in a 90-slide production run (`docs/slidespec-production-quality-speed-analysis-20260805.md`) | Families can **drift**: fixing a defect in the base does not fix members. Maintenance cost grows with packs × canvases |
| **C. Generation-time composition without frames** (authored free design with identity as style, i.e. `brand-style` mode) | Every canvas immediately; no approval step; the authored driver is already canvas-aware | `registered-template` promises pre-approved fixed layouts with deterministic capacity admission and receipts. C gives neither, and output varies per deck | C would serve canvases a pack has no member for today. Offering C as an explicit fallback instead of rejection is open decision 8 |
| **D. Contain / letterbox** (`document_canvas_contain`) | Zero work; exact fidelity | Letterboxing wastes the canvas, which is what decision 9 rejected | — |

---

## 6. Implementation plan

Stages are sequential except where noted. Each lists files, tests and acceptance criteria.

**S0: Prerequisites** (blocking; also blocks any variant).
- `source_native_generation.py`: capture at the pack canvas when `_document_canvas` is absent. Replace
  the `_SLIDE_WIDTH_PX` and `_SLIDE_HEIGHT_PX` reads with the pack canvas.
- `source_native_canvas.py`: a canvas session (scale 1) for preset canvases.
- `design_packs.py`: canvas-parameterised `_authored_frame` and `/from-presentation` (canvas from the
  presentation's persisted canvas).
- Verify, and add if missing, `quality_pages` in the source-native result.

Tests:
- The 16:9 base run is byte-identical (receipts, digests).
- A hand-made 720×720 authored pack generates a 1:1 deck captured at 720×720.
- `/from-presentation` on an A4 deck yields a 1188×840 source.

Acceptance: `tests/unit/test_canvas_admission_baseline.py` unchanged; the new tests pass; one live 1:1
source-native deck.

**S1: Family model.**
- `design_pack/family.py` (identity and contract schemas, sealing).
- `manifest.py` (v3, file roles `provenance` and `check-report`).
- `registry.py` (`family`, `members`, skip legacy `variants/`).
- `variants.py` → `members.py` (resolution per §2.4, `design_pack_canvas_unsupported`).
- `generation_route.py` (seal `familyAnchorDigest`).
- Both admission call sites.
- Delete `variant_source.py` and the re-layout half of `variant_store.py`.

Tests: rewrite `test_design_pack_variants*.py` as `test_design_pack_family*.py`, covering:
- v1/v2/v3 sealing;
- anchor and member pin resolution;
- rejection codes;
- immutability of approved members;
- a registry that tolerates stale `variants/`.

Acceptance: e2e admission (as in `e2e_verify.py`) with a hand-written member, now also **generating**
one deck.

**S2: Identity extraction.** `design_pack/identity_extract.py` (authored and PPTX) and the CLI commands
`family init`, `identity show` and `identity seal`.

Tests: golden identity for the three trial packs (the palette contains `#173A63`; a pack without chrome
yields no chrome intents); a PPTX fixture with a theme and a master logo.

Acceptance: a reviewer can edit `imagery` and `notes`, and sealing is reproducible byte for byte.

**S3: Member drafting.**
- `design_pack/member_source.py` (plan and frame prompts, extraction, compile).
- `utils/llm_calls/design_pack_member.py` (prompts reusing `author_canvas_shape` and the design-system
  rules).
- `member draft --source generate|presentation:<id>|pptx:<path>`.

Tests: plan validation against the contract with a stubbed LLM (`tests/mocks/`); extraction honouring
`data-slot-role`; drop-frame-when-contract-holds logic; provenance contents.

Acceptance: drafts for 3 trial packs × {a4-landscape, square-1x1, ultrawide-21x9}, with **at least 7 of
9 passing all hard checks** and every failing one diagnosable from its report.

**S4: Checks and approval.**
- `design_pack/member_checks.py` (H1–H6, S1–S5, fill generators).
- `member check`, `approve` and `reject`, and the review sheet.

Tests: each check has a passing and a failing fixture. The re-layout drafts must fail S1 on 21:9 and
S2/S3 on 1:1 (the calibration evidence).

Acceptance: thresholds are recorded in the check report schema, and approval refuses hard failures.

**S5: Bench integration** (can run parallel with S4).
- `canvas_bench.py` and `canvas_bench_live.py`: add `--design-pack-pin` and `--generation-mode`.
- `canvas_bench_assess.py`: pool by family anchor.

Tests: pooling and key tests in `tests/unit/test_canvas_bench.py`.

Acceptance: one family member reaches a `candidate` verdict, or a clear `hold` or `fail`, on the
isolated backend.

**S6: Exposure.** The admission/UI picker lists, for the pinned pack, the canvases with approved
members. The a4-landscape member comes first, because A4 landscape is the product default.

Acceptance: the admission baseline fixture is updated only by the new admitted rows.

### Open decisions for the user

1. Pin semantics: anchor digest names the family (recommended), or a new family digest.
2. Whether base renders are shown to the generator as a style reference. This helps identity fidelity
   but risks it copying 16:9 composition. Recommended: yes, labelled "no positions".
3. Retire the variant store outright, deleting drafts after a scan (recommended), or keep it read-only
   for one release.
4. The G-contract minimum set (cover, content, closing plus parity) versus a fixed full role set.
5. Whether to re-register the existing bases with semantic slot roles first. They fail G2 on 9 of 10
   frames, so their own page lane is weak and members will be structurally cleaner than their base.
6. Whether UI exposure needs the bench `candidate` tier (about 12 candidate + 18 baseline decks per
   member) or human approval only.
7. Invest in a PPTX executor or PPTX-to-authored conversion so customer PPTX members (and PPTX bases)
   can generate.
8. Whether a canvas without a member falls back to `brand-style` generation (alternative C) instead of
   rejecting.

---

## 7. Self-review

| Axis | Score /10 | Reasoning |
|---|---|---|
| Output quality potential | 7 | Removes the structural causes of the trial failures (fixed element set, re-placement) and adds measured gates that catch them. The ceiling is the LLM's template-design quality, which is unproven; members may look like competent generic templates in brand colours |
| Fidelity to customer templates | 6 | Option 1 gives full fidelity. Option 2 gives **identity-level fidelity only**: palette, type, logo, chrome and component treatment, not the customer's compositions. A brand owner who equates "our template" with "our layouts" may reject generated members. Lower than re-layout on layout resemblance |
| Complexity | 4 (high cost) | New identity extractor, contract, v3 manifest, generator, 11 checks, CLI and bench pooling; roughly 2.5–3.5k lines plus tests. S0 touches the largest file in the lane (4,168 lines) |
| Risk | 5 | Contained, because members are additive and admission stays fail-closed. Risky around S0 (executor geometry constants), threshold calibration from only three bases, and a gate whose primary bench metric may be unmeasured for this lane |

**Top problems:**
1. **S0 is a hidden prerequisite for any non-16:9 pack.** The source-native executor captures preset
   canvases at 1280×720. `17059ccb6`'s variants also could not execute (frame html dropped). Nothing
   non-16:9 in this lane has ever generated a deck, so the first live run will surface more.
2. **The bases are deck snapshots, not templates.** They have 22–109 unlabelled `body` slots per frame,
   one title in total, empty tokens and in one case no chrome. Identity extraction has little to work
   with, the contract cannot be anchored on base semantics, and members will be cleaner than their
   base. Cross-canvas decks from one pin will differ in structure, not just shape.
3. **"As good as the base" is not measurable yet.** The bench has no pin dimension, and the
   source-native lane's `quality_pages` output is unverified. Until S5, approval rests on automated
   checks and one reviewer.
4. **Customer PPTX (option 1b) cannot generate at all**, at any canvas. The highest-fidelity source is
   blocked by a pre-existing gap outside this design.
5. **Review cost scales with packs × 6 canvases** and families drift from their base over time. This is
   the price of rejecting the responsive single-pack alternative.
6. **Reach is narrow.** Packs are authored-only and `registered-template`-only, while the product default
   strategy is svg, which never admits packs. The investment serves a minority path unless open decision
   8 or svg pack support widens it.
