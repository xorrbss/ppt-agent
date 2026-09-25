# Authored composition envelope profiles (2026-09-12)

One knob, `AUTHORED_COMPOSITION_ENVELOPE_PROFILE`, selects the density contract
an **ordinary** authored deck is written under: `current` (default, today's
behaviour exactly) or `august` (the pre-2026-08-28 contract). It exists so the
judgement "the current decks are worse than the August ones" can be tested as
an A/B instead of argued, and so the answer is one setting rather than a set of
per-rule flags a reader has to assemble correctly.

## 1. Inventory: what was added between 2026-08-28 and 2026-09-03

Verified with `git log`/`git log -S` on this branch (`serve/main-20260902`).
Line numbers are as of this commit.

### 1.1 The frozen composition envelope

| | |
|---|---|
| Introduced | `046764c27` (2026-08-28, *feat(authored): harden durable quality evidence*) — the commit that created `services/authored_composition_policy.py` (+625 lines) |
| Where | budgets: `services/authored_composition_envelope_profile.py:93` (`_ROLE_BUDGETS`, moved there by this change from `authored_composition_policy.py`); font floor: `services/authored_composition_policy.py:127` (`SlideCapacity.min_font_px = 15`); the brief the model is given: `services/authored_composition_policy.py:162` (`SlideCapacity.prompt_brief`) |
| What it states | per-role `(title, body, visible, qids)` ceilings — the default role is `(60, 380, 460, 40)`, i.e. visible text ≤ 460 chars and ≤ 40 semantic targets — a 15px font floor, and an image-slot ceiling, all handed to the author as `SERVER-FROZEN COMPOSITION ENVELOPE (do not negotiate)` and re-checked after render by `capacity_violations` (`authored_composition_policy.py:1307`) and before render by `aggregate_owner_capacity_violations` (`:718`) |
| August value it replaced | **none**. Before `046764c27` no envelope existed: no character, semantic-target, image or font budget was stated to the model or checked afterwards. The 460/40 figures are not a loosening of an August number — the recorded August medians (417–748 visible chars, 29–48 text leaves, 12.5–16.5px) are measurements of an *unbudgeted* composition |
| Drift since | `min_font_px` was **16** at creation and lowered to 15 in `c52e2a7ce` (2026-08-30); `CLOSING`/`AGENDA`/`PROBLEM`/`PILLARS`/`OUTCOMES`/`ROADMAP`/`ARCHITECTURE` budgets were all raised between 08-28 and 09-06 (e.g. `PROBLEM` 310/400/28 → 380/460/40) |
| Off switch | `AUTHORED_COMPOSITION_ENVELOPE_ENABLED`, added `c7b6383c1` (2026-09-05, *feat(authored): author ordinary decks by free composition again*), **default false** |

**Status: already off by default.** For an ordinary deck the envelope is
`enforced=False`, the prompt brief is `""`, both capacity gates return `()`, and
the floor is the 12px capability-registry floor (`AUTHORED_FONT_FLOOR_PX`,
`services/authored_vnext/capability_registry.py:61`). Pinned by
`tests/unit/test_authored_free_composition_default.py`.

### 1.2 The figure plan and the archetype library contract

| | |
|---|---|
| Introduced | `7c95334e1` (2026-09-03, *feat(authored): plan each slide's figure and draw it from a component library*) |
| Where | seam: `services/authored_deck_plans.py:66` (`_infographic_plan_applies`); node/label limits: `models/authored_infographic_plan.py:105-107` (`MAX_TAKEAWAY_CHARS = 12`, `MAX_NODE_LABEL_CHARS = 10`, `MAX_NODE_DESC_CHARS = 18`) and `:114-115` (`MIN_NODES = 3`, `MAX_NODES = 6`); exclusivity clause: `utils/authored_infographic/contracts.py:231` ("no other diagram, card grid or chart may compete with the figure"); hero weight (the page *is* its figure): `utils/llm_calls/author_infographic_plan.py:88` |
| What it states | one server-drawn figure per planned page, 3–6 nodes, each node's label ≤ 10 chars / description ≤ 18 / the page's takeaway ≤ 12, nothing else on the page may compete with it, and a `hero` page carries no side text |
| August value it replaced | **none**. Before `7c95334e1` each slide decided its own picture with no deck-wide figure, no node count, no label ceilings and no exclusivity |
| Off switch | `AUTHORED_INFOGRAPHIC_PLAN_ENABLED`, added `c7b6383c1` (2026-09-05), **default false** |

**Status: already off by default** — measured as "visibly thinner than free
composition", which is why it was switched off five days after it landed.

### 1.3 The strict DOM gate — the one with no switch

| | |
|---|---|
| Introduced | `046764c27` (2026-08-28), extended to the source profiles in `2971728b6` (2026-08-29) |
| Where | **set**: `services/authored_composition_policy.py:287` (`resolve_composition_profile`) — `strict_dom_gate` was hard-coded `True` for all three profiles. **Read**: `services/authored_presentation_generation.py:2832` (the final pre-export gate), `services/authored_presentation_pipeline_helpers.py:19` (per-slide evaluation before each durable checkpoint), `services/authored_style_anchor_stage.py:205` (the anchor gate), `services/authored_terminal_slide_policy.py:55,60` (terminal-slide policy) |
| What it does | turns `inspect_authored_dom` findings into a **publication gate**: a slide whose findings fall in `BLOCKING_SAFETY_CATEGORIES` is repaired, remade, flagged, or the deck fails closed |
| August value it replaced | DOM findings were **advisory**. `0730e7077` (2026-08-05, *fix(remake): treat candidate DOM findings as advisory, not fatal*) is the August contract in one sentence |
| Off switch | **none, before this change** |

This is the answer to "the August behaviour is unreachable by config". The
envelope and the figure plan already had switches; the gate did not.

Ceilings added to the gate *after* 08-28 that only bind while it is on, and are
therefore covered by the same switch: `3881426ed` (text escaping its card,
2026-09-06), `f0d2b126c` (copy running into the footer safe zone, 2026-09-06),
`417e53dfc` (one title owner per slide, 2026-09-06), `306391ea6` (the footer
detector demoted to advisory, 2026-09-06).

### 1.4 The visual story plan (inventoried, not wired)

`utils/llm_calls/author_deck.py` `_visual_story_plan_applies` freezes a story
kind and silhouette per slide. Switched off by `22525d948` (2026-09-05) behind
`AUTHORED_VISUAL_STORY_PLAN_ENABLED`, **default false**. Its seam is in an
`llm_calls` module outside this change's ownership; it is left to its own knob
and named here so the inventory is complete.

### 1.5 Summary

| Ceiling | Added | August value | Bound an ordinary deck today? |
|---|---|---|---|
| Composition envelope (460 chars / 40 qids / 15px) | 2026-08-28 `046764c27` | did not exist | no — `AUTHORED_COMPOSITION_ENVELOPE_ENABLED` off |
| Figure plan + library contract (3–6 nodes, 10/18/12 chars, exclusive) | 2026-09-03 `7c95334e1` | did not exist | no — `AUTHORED_INFOGRAPHIC_PLAN_ENABLED` off |
| Visual story plan | ≤ 2026-09-05 | did not exist | no — `AUTHORED_VISUAL_STORY_PLAN_ENABLED` off |
| **Blocking DOM quality gate** | 2026-08-28 `046764c27` | findings advisory (`0730e7077`) | **yes — unconditional** |

**Correction to the recorded context.** The task brief (facts recorded
2026-09-05) states that the envelope and the library contract "have no off
switch". `c7b6383c1` and `22525d948` landed on 2026-09-05 and gave all three
their switches, defaulted off. What genuinely had no switch, and still shapes
every ordinary deck, is the strict DOM gate.

## 2. The knob

`AUTHORED_COMPOSITION_ENVELOPE_PROFILE` — registry `enum`, choices
`current` | `august`, default `current`, target `fastapi.runtime`, group
`authored.quality` (`servers/fastapi/constants/settings_registry.json`).
Accessor: `utils/get_env_programme.py::get_authored_composition_envelope_profile`.

The registry validates the enum on read, so a misspelled, differently-cased or
space-padded value raises before it reaches any policy — a silently wrong
profile would invalidate the comparison the knob exists for.

## 3. The profile table

`services/authored_composition_envelope_profile.py` resolves the knob into an
immutable `CompositionEnvelopeProfile`. Every field is **ordinary-deck scoped**.
`None` means "state nothing; the per-rule knob that already decided this still
decides it" — which is how `current` is byte-identical to the behaviour it
replaces.

| field | `current` | `august` | consumed by |
|---|---|---|---|
| `ordinary_envelope_enforced` | `None` → defers to `AUTHORED_COMPOSITION_ENVELOPE_ENABLED` | `False` | `authored_composition_policy.build_authored_capacity_plan` |
| `ordinary_strict_dom_gate` | `True` | `False` | `authored_composition_policy.resolve_composition_profile` → every reader of `CompositionProfile.strict_dom_gate` |
| `ordinary_infographic_plan` | `None` → defers to `AUTHORED_INFOGRAPHIC_PLAN_ENABLED` | `False` | `authored_deck_plans._infographic_plan_applies` |

Not in the table, deliberately:

- **The role budget numbers.** There are no "August budgets" to restore (§1.1);
  what `august` changes is whether the envelope binds, never its numbers. The
  table lives in the same module as a module constant so both profiles and both
  source-fidelity profiles read one copy.
- **The font floor.** It follows from enforcement: a relaxed envelope already
  yields the 12px `AUTHORED_FONT_FLOOR_PX`, and a bound one 15px.

### Resolution, once per operation

`resolve_envelope_profile()` is a pure read of the request/task-scoped,
immutable `RuntimeSettings` snapshot, so two reads inside one operation cannot
disagree. Both seams also accept an explicit `envelope=` argument, so a caller
that has already resolved the object passes it down and the ambient environment
cannot contradict it mid-operation (pinned by
`test_an_explicit_envelope_overrides_the_ambient_one`).

### Precedence

`august` **overrides** the three per-rule knobs: setting
`AUTHORED_COMPOSITION_ENVELOPE_ENABLED=true` alongside `profile=august` still
yields an unbound deck. "The deck as August authored it" cannot be partly
2026-09.

### Scope: ordinary decks only

`source-verbatim` and `source-outline` keep their envelope **and** their strict
DOM gate under both profiles. Theirs is a fidelity contract, not a density
preference: the envelope is what makes an impossible source page fail closed
instead of silently losing source text, and the gate is what makes a clipped or
dropped source page fail rather than publish. Pinned by
`test_august_does_not_reach_the_source_fidelity_profiles`.

## 4. What `august` re-enables

1. **Composition is the model's again, unconditionally.** No character,
   semantic-target, image or font budget is stated in the author prompt, none is
   checked before or after render, and the floor is 12px. Already the default;
   `august` makes it unoverridable for the run.
2. **DOM findings become advisory again** for ordinary decks — the 2026-08-05
   contract. Slides are no longer repaired, remade, flagged or failed on
   geometry findings by the per-slide, anchor, terminal and final gates, because
   all four read the one `strict_dom_gate` flag this profile sets.
3. **No deck-wide figure is planned**, so no 3–6-node / 10–18–12-char / "nothing
   may compete" contract is imposed on any page.

## 5. What `august` cannot restore

`august` changes decisions, not code. It does **not** bring back:

- **Deleted code.** Anything removed between 08-28 and today is gone; the knob
  only chooses among code paths that still exist. Restoring a removed path is a
  revert, not a setting.
- **Prompt text.** The author prompt has been rewritten many times since August
  — the geometry paragraph (`utils/authored_geometry_brief.py`, 2026-09-05), the
  semantic-role vocabulary and the 12px content floor
  (`utils/llm_calls/author_slide_brief.py:32`), the header contract, the fill
  directive. Those are not behind this switch, are not behind any switch, and
  are the most likely remaining cause of a density difference the three knobs do
  not explain. A prompt A/B is a separate change.
- **The model.** August decks were authored by whichever model was default then;
  the current default is `gpt-5.6-sol`. Composition follows the model at least
  as much as the policy, so an `august`-vs-`current` run is only a clean
  comparison at a fixed model.
- **Non-composition machinery added since.** The local fitter, the illustration
  share budget, the style anchor, admission, the repair loop and the export path
  all changed independently and keep their own knobs.
- **Source-fidelity decks.** Out of scope by design (§3).
- **Any safety guarantee.** With the gate off, a clipped, overflowing or
  overlapping slide publishes unflagged, exactly as in August. That is the point
  of the comparison and the reason `current` stays the default.

## 6. Verification

- `current` is byte-identical: every seam either repeats today's constant or
  defers to the knob that already decided it; pinned by the `current` half of
  `tests/unit/test_authored_composition_envelope_profiles.py`, and by the
  unchanged `tests/unit/test_authored_free_composition_default.py`,
  `test_authored_composition_policy.py`, `test_composition_capacity_regions.py`,
  `test_source_verbatim_capacity_admission.py`,
  `test_authored_quality_receipt_relaxed_capacity.py`,
  `test_authored_infographic_plan.py`, `test_authored_style_anchor_stage.py`.
- `august` yields the August limits: `test_august_yields_the_august_limits`,
  `test_august_turns_the_blocking_dom_gate_off_for_ordinary_decks`,
  `test_august_overrides_the_per_rule_knobs`.
- The role-budget table moved modules without a number changing:
  `test_role_budgets_are_unchanged_by_the_extraction`.
