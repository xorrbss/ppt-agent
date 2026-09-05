# Authored richness regression: 2026-08-19 vs 2026-09-04/05

Scope: the `strategy=authored` path with `authored_style=hyundai-culture-center`.
Question: which code changes between the commit live on 2026-08-19 and HEAD caused
the visible loss of richness, and which of them belong to the infographic library
that landed on 2026-09-03.

Everything below is derived from git history plus the stored HTML of the August
deck (`presentations.id = 2c89713d-ce17-4415-8c72-c5ae2cda7002`, read-only).

## The baseline commit

`2c89713d` was created at **2026-08-19 07:46:38Z = 16:46 KST**. The last commit
that landed before that moment is `88fc77141` (*fix: never offer a slide's own
backplate as a source image*, 15:55 KST). `6d84f34a4` landed at 16:50 KST, four
minutes after the request was accepted — its effect on that deck's later slides
is uncertain, so it is listed but not relied on.

Everything else discussed here landed **after** the August deck.

## Timeline

| when (KST) | commit | what it changed on the authored path |
|---|---|---|
| 08-19 15:55 | `88fc77141` | **baseline.** No illustration plan, no infographic plan, no story plan, no composition envelope, no role token budgets. Every slide composed itself. |
| 08-19 16:50 | `6d84f34a4` | Narrowed `authored_styles/hyundai-culture-center.yaml` `illustration.style_prompt`: dropped "dense but disciplined consulting-grade information design, module-and-connector system maps, gantt style schedule rails" for "restrained flat vector finish, clean edges, generous whitespace". |
| 08-19 23:26 | `f994ba36b` | Added the deck-scoped **illustration plan** and `illustration_marker()`. A non-selected slide is now told: *"ILLUSTRATION FOR THIS SLIDE: NONE … emit no `<img>` element and no `data-illust-prompt` anywhere on it."* Budget: 15 % of the deck, floor 3, ceiling 14. |
| 08-22 | `cc785a34d` | Added `visual_story_marker()` + `_STORY_RULES` in `author_slide_prompt.py`, plus the deterministic `plan_authored_visual_story()` in `author_deck.py`. Every slide now receives a frozen `story_kind` and `silhouette`, and a one-line rule for that kind. Also introduced the mandatory `data-qid` on every visible element. |
| 08-28 | `046764c27` | Added `services/authored_composition_policy.py`: the **SERVER-FROZEN COMPOSITION ENVELOPE** with per-role caps on title chars, body chars, total visible text and number of semantic elements (`max_qids`). |
| 08-29 | `2971728b6` | Added `_FAST_ROLE_OUTPUT_BUDGETS` in `author_slide.py`: COVER 10 000 tokens, SECTION 8 000, AGENDA 8 000, CLOSING 6 000 — and `reasoning_effort="minimal"` for all four (content slides keep 16 000 / "low"). |
| 09-02 | `ec66b4e9c` | Added `utils/llm_calls/author_batch.py`. On the `token` rate profile (personal-token providers) 3 slides are authored **per provider call** (`_TOKEN_SLIDES_PER_CALL = 3`). |
| 09-03 | `7c95334e1` | **The infographic library.** `utils/authored_infographic/` (skeleton, contract, checker, CSS), `author_infographic_plan.py`, `author_infographic_marker.py`, `services/authored_deck_infographic_plan.py`. Also rewrote the illustration plan: it is no longer decided, it is **derived** — a slide gets a picture only when its archetype is `scene`. |
| 09-03…09-04 | `3c6108729`, `53e73efda`, `803b76f05`, `37012f738`, `feae4aeab`, `aff0f444e` | Content-box arithmetic rules, header rules, retry hints, capacity/title recovery, provider-overload recovery. Not richness-relevant. |

There is **no environment knob** for any of this: `grep -i infographic
utils/get_env.py constants/settings_registry.json` is empty, and
`_infographic_plan_applies()` (`services/authored_deck_plans.py:60`) returns True
for every deck that is not an exact reconstruction or a source reflow.

## Evidence from the August deck

Slide 6 (`1 002 587` bytes stored, `10 275` bytes after eliding the embedded JPEG):

* A hand-authored bar chart with its own CSS class system — `.chart-area`,
  `.grid-line`, `.grid-label`, `.bar`, `.bar-a/.bar-b`, `.year`, `.legend-line`,
  `.current-pill`, `.chart-note` — axis rules, four gridlines, five axis labels,
  six bars across three year groups, a two-item legend and two data-label pills.
* Beside it, two metric cards with progress rules, and a **full illustration**:
  a 1408×768 isometric editorial scene (desk with reports, charts, floor plans).
* **65 `data-qid` elements** on one slide.

Slide 0 (the cover, 7 141 bytes): two rotated gradient fields (`.glow-one`,
`.glow-two`), a decorative orbit/node/core graphic (`.tech-field`, `.orbit-a/b`,
`.core`, `.node`), an eyebrow row with a marker bar, a two-tone `h1`, a descriptor
and two `.meta-block` info cards. **32 `data-qid` elements.**

Both were produced with **no plan of any kind** telling the model what to draw.

## The losses

### 1. Illustrations: 74/77 → a handful

| stage | rule |
|---|---|
| baseline | no marker at all; the `ILLUSTRATION SLOT` rules in the design system allow **at most one** slot per slide, and the model used it almost everywhere |
| `f994ba36b` | a deck-level budget (15 %, min 3, max 14) selects the slides; every other slide is explicitly forbidden an `<img>` |
| `7c95334e1` | the budget is gone; `derive_illustration_plan()` returns exactly `plan.scene_indices()` — a picture **iff** the archetype is `scene` |

`models/authored_infographic_plan.py`:

```python
MAX_SCENE_SHARE = 0.25
MIN_SCENE_CEILING = 1
SCENE_FALLBACK_ARCHETYPE: Archetype = "icon_list"

def scene_ceiling(content_slide_count: int) -> int:
    return max(MIN_SCENE_CEILING, round(MAX_SCENE_SHARE * content_slide_count))
```

and the plan prompt further discourages it: *"A scene is the only figure that
spends a generated picture; use it only when the content names a concrete thing a
reader would want to see, and never for an abstract concept."* Cover, closing,
section and agenda are forced to `none` before the model is even consulted
(`NON_FIGURE_ROLES`, `_repair_slide`).

So on a 20-slide deck the ceiling is about 4 illustrations, and the plan's own
prose will normally choose fewer. The `<img>` elements that today's slides *do*
carry are mostly **not illustrations**: `services/authored_infographic_assets.py`
swaps each `.ig-ic` placeholder for a small generated transparent PNG
(`<div class="ig-ic"><img src="data:…" alt=""></div>`, line 165). Icons replaced
illustrations one-for-one in the markup while being a different thing entirely.

Secondary: `6d84f34a4` also rewrote what a surviving picture looks like — the
style prompt no longer asks for "system maps" or "gantt schedule rails".

**Cause: `f994ba36b` (the marker + budget) compounded by `7c95334e1` (scene-only
derivation + 25 % ceiling). Not the library's rendering, but the library's plan.**

### 2. Free composition on content slides

`utils/authored_infographic/contracts.py` hands the model finished markup and
forbids redesign:

> 1. Paste the following figure into the slide body EXACTLY as given — every
>    element, class, data-qid, data-native, data-qid-ignore and text …
> 2. … Do NOT write CSS for any `.ig*` class, do NOT add a class the library does
>    not define, and do NOT put a style attribute or a `<style>` element inside the
>    figure …
> 4. Everything outside the figure … is yours to author … but **no other diagram,
>    card grid or chart may compete with the figure.**

This is enforced, not advisory. `utils/authored_infographic/checker.py` rejects
the document and `enforce_infographic_contract` raises
`InfographicContractError`, whose message becomes the retry's correction hint:
exactly one `.ig` root, the planned root and size classes, no class outside
`library_classes()`, no inline style except `left/top/width/height` on
`.ig-co`/`.ig-pin`, the planned labels verbatim and in order, the planned node
count.

Node text is also clipped hard before it reaches the model:
`MAX_TAKEAWAY_CHARS = 12`, `MAX_NODE_LABEL_CHARS = 10`, `MAX_NODE_DESC_CHARS = 18`.
That is what makes today's pages read as terse icon/label card rows.

A second, independent narrowing came earlier from `cc785a34d`: every slide is
handed a frozen `story_kind` and `silhouette` from a fixed table (`_SILHOUETTES`
in `author_deck.py`), with "Do not reuse a neighbouring slide's silhouette".

**Cause: `7c95334e1` (library-specific), with `cc785a34d` narrowing composition
already before it.**

### 3. Charts

Three independent reasons a slide like August's slide 6 cannot happen today.

**(a) The archetype vocabulary has no chart.** `ARCHETYPES` in
`models/authored_infographic_plan.py` lists 20 shapes — process, cycle, hierarchy,
layers, comparison, before_after, timeline, roadmap, funnel, pyramid, matrix, kpi,
gauge, icon_list, checklist, hub_spoke, venn, swimlane, branch, scene. There is no
bar/line/area chart. A three-year two-series financial trend is planned as `kpi`
(a row of big numbers) or `before_after`/`comparison` (a real `<table>`).

**(b) The one prompt line that asked for a chart is now unreachable.** `cc785a34d`
added `_STORY_RULES["dataChart"]`:

> "Turn only the numeric series stated in CONTENT into a labelled HTML/CSS chart
> and pair it with one explicit takeaway."

But `7c95334e1` inserted the archetype lookup **above** the `dataChart` branch in
`_story_kind()` (`utils/llm_calls/author_deck.py:835`):

```python
if infographic is not None and infographic.archetype in _ARCHETYPE_STORY:
    return _ARCHETYPE_STORY[infographic.archetype]
...
fact_count = len(_DATA_FACT_PATTERN.findall(content))
if fact_count >= 3 and (...):
    return "dataChart"
```

`_ARCHETYPE_STORY` maps `kpi -> "metric"` and `gauge -> "metric"`. So a numeric
slide that would previously have reached `dataChart` is intercepted and told
"metric" instead. `dataChart` is reachable only when the plan returned no
archetype at all for that slide.

**(c) Even outside the figure, a chart is forbidden.** Contract item 4 above.

**(d) Size.** The August chart slide carried 65 `data-qid` elements. Today's
`_ROLE_BUDGETS` (`services/authored_composition_policy.py:205-220`) cap the
default role at **40**, and `authored_composition_policy.py:1560` turns an excess
into a `qids:65>40` violation that drives a capacity-recovery retry.

Note that August's chart came from *free composition*, not from a chart
instruction: `visual_story_marker` did not exist yet. The model reached for a
chart on its own.

**Cause: `7c95334e1` for (a)(b)(c), `046764c27` for (d).**

### 4. The cover

The cover is handled by three separate role contracts, all added after August.

1. **Story rule** (`cc785a34d`, `author_slide_prompt.py:349`):
   `"cover": "Keep the cover minimal: one decisive title hierarchy and one focal field."`
   The August cover had a diagonal field, an orbit graphic, an eyebrow row, a
   two-tone title, a descriptor and two info cards — five focal elements.

2. **Quiet-page figure marker** (`7c95334e1`). `NON_FIGURE_ROLES = {"COVER",
   "CLOSING", "SECTION", "AGENDA"}` forces `has_figure = False`, and
   `author_infographic_marker.infographic_marker()` then says:

   > "FIGURE FOR THIS SLIDE: NONE. This deck's infographic plan set this page as a
   > QUIET page of type: **no diagram, no card grid, no icon row.** Set the content
   > with a decisive hierarchy and generous whitespace."

   The August cover's two `.meta-block` cards are a card grid.

3. **Composition envelope** (`046764c27`): `"COVER": (58, 220, 280, 22)` — title
   at most 58 chars, body 220, total visible text 280, **at most 22 semantic
   elements**. The August cover has **32**. It would not pass today's gate.

4. **Token/reasoning budget** (`2971728b6`): COVER is authored with 10 000 output
   tokens and `reasoning_effort="minimal"` instead of 16 000 / "low".

On the specific overlap you saw (`position:absolute; top:96px` under a two-line
title): no rule was removed. `author_slide_rules.py` still carries *"No fragile
positioning: do NOT hard-pin a block to a fixed top/absolute offset that a longer
heading could overlap"*, and `3c6108729` strengthened the surrounding arithmetic
rules. The most likely cause is therefore (4) — the cover is the one role whose
reasoning effort was dropped to "minimal", and hand-computing a `top` under a
wrapping title is exactly the kind of mistake a non-reasoning pass makes. This is
an inference from the budget change, not something the code states.

### 5. Contributing, not proven

`ec66b4e9c` batches 3 slides into one provider call on the `token` rate profile
(`_TOKEN_SLIDES_PER_CALL = 3`, `MAX_BATCH_SLIDES = 3`), sharing one `max_tokens`
and one reasoning level across three complete documents
(`author_batch.py:347`). August authored one slide per call. This plausibly
dilutes per-slide effort, but nothing here measures it. Worth an A/B with
`AUTHORED_BATCH_SLIDES_PER_CALL=1` before drawing a conclusion.

## What disabling the library would and would not recover

"Disabling the library" here means making `_infographic_plan_applies()` return
False (or `resolve_infographic_plan()` return `None`) — the codebase has no knob,
so this is a code change. Every consumer already treats `None` as "no plan".

**Recovered:**

* **Free composition on content slides.** No plan means no `infographic_marker`,
  no `slot_contract`, no checker (`finalize_infographic_slide_html` returns the
  HTML untouched when `slide is None`). The model composes the whole page again.
* **Illustrations — most of the way back.** `derive_illustration_plan(None)`
  returns `None`, `illustration_allowed` becomes `None`, and
  `illustration_marker(None)` returns `""`. The prompt is then byte-identical to
  the pre-plan prompt on this axis: any slide may author its one slot. This is the
  single highest-leverage consequence, and it is somewhat accidental — the
  illustration plan was folded into the infographic plan, so switching one off
  switches both off.
* **Charts, partially.** (a) and (c) disappear with the plan, and (b) unblocks:
  `_story_kind()` falls through to the `dataChart` branch again, so numeric
  OUTCOMES/timeline/comparison slides get the "labelled HTML/CSS chart" rule that
  `cc785a34d` wrote and `7c95334e1` made unreachable.
* **The cover's "no card grid, no icon row" prohibition** (loss 4, item 2).

**Not recovered:**

* **Cover minimalism.** `_STORY_RULES["cover"]` (`cc785a34d`) is applied by
  `plan_authored_visual_story()`, which is deterministic, unconditional and
  independent of the infographic plan.
* **The composition envelope.** COVER `max_qids = 22` vs August's 32, default role
  `max_qids = 40` vs August's 65, COVER visible text at most 280 chars. These come
  from `046764c27` and are enforced by `authored_composition_policy` and the v2
  quality receipt regardless of any plan.
* **Cover/section/agenda/closing reasoning effort and token budget**
  (`2971728b6`) — the probable cause of the absolute-positioning slip.
* **Silhouette rotation and the "do not reuse a neighbouring silhouette" rule**
  (`cc785a34d`).
* **Illustration look.** `6d84f34a4` narrowed the style prompt; a picture that
  does get generated will still be a restrained flat vector, not the dense
  consulting diagram August produced.
* **Batching** (`ec66b4e9c`), if it is contributing at all.

## If the library is to be kept

The library is not the sole cause of any loss except (2) free composition. A
version that keeps the library and restores richness would need, roughly in order
of expected impact:

1. **Decouple the illustration plan from the archetype plan.** Restore an
   independent picture budget (the `f994ba36b` shape, or higher) instead of
   `scene`-only derivation, so a `process` or `pillars` figure page can still
   carry an illustration.
2. **Add a chart archetype**, or let `_story_kind()` prefer `dataChart` over
   `_ARCHETYPE_STORY` when the content carries a real numeric series, and relax
   contract item 4 for that case.
3. **Exempt COVER (and SECTION/CLOSING) from the quiet-page prohibition** and
   from `_STORY_RULES["cover"]`; raise `_ROLE_BUDGETS["COVER"]` past 22 qids.
4. **Give COVER back 16 000 tokens and "low" reasoning** — the cheapest single
   change, and the one most likely to fix the observed overlap.
5. Re-measure with `AUTHORED_BATCH_SLIDES_PER_CALL=1`.

## Files worth reading

* `servers/fastapi/utils/authored_infographic/contracts.py` — the slot contract
* `servers/fastapi/utils/authored_infographic/checker.py` — the enforcement
* `servers/fastapi/utils/llm_calls/author_infographic_marker.py` — the per-slide brief
* `servers/fastapi/utils/llm_calls/author_infographic_plan.py` — the archetype vocabulary
* `servers/fastapi/models/authored_infographic_plan.py` — `NON_FIGURE_ROLES`, `MAX_SCENE_SHARE`, node char budgets
* `servers/fastapi/models/authored_illustration_plan.py` — the derived plan
* `servers/fastapi/utils/llm_calls/author_slide_prompt.py` — `illustration_marker`, `visual_story_marker`, `_STORY_RULES`
* `servers/fastapi/utils/llm_calls/author_deck.py:804-860` — `_ARCHETYPE_STORY`, `_story_kind`
* `servers/fastapi/services/authored_composition_policy.py:140-270` — the envelope
* `servers/fastapi/utils/llm_calls/author_slide.py:80-90, 890-910` — role token budgets
