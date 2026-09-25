# Authored author-prompt profiles (2026-09-12)

One knob, `AUTHORED_AUTHOR_PROMPT_PROFILE`, selects which **authoring prompt
text** a slide is written from: `current` (default, today's prompt byte for
byte) or `august-0819` (the 2026-08-19 text, with every contract the gates have
added since kept in place). A second field on the same profile,
`AUTHORED_AUTHOR_PROMPT_MODEL`, pins the authoring model, because composition
follows the model at least as much as the prompt and an A/B that lets both move
measures neither.

It exists because
`docs/authored-composition-envelope-profiles-design-20260912.md` closed off the
other suspects: the composition envelope, the figure plan and the visual-story
plan are all **already off by default**, so they cannot be what changed between
the owner's 2026-08-19 deck and today's. That document named the prompt
rewrites and the model change as the remaining explanation and said a prompt
A/B was a separate change. This is that change.

The reference figure in the brief — 5 440 input tokens per call on 2026-08-19,
~12 700 today — is a receipt measurement that includes the slide's CONTENT and
every per-slide block. What is measurable here from source alone is characters,
and section 2 measures them.

## 1. What this repository can and cannot count

There is **no token counter in this repository**. `tiktoken` is not a
dependency; the only tokenizer present is the `tokenizers` wheel pulled in
transitively, with no vocabulary for the models in use, and the vendored
Anthropic SDK's `count_tokens` is a network call. Every figure below is
therefore a **character count**, measured by rendering the real builders. For
the mostly-English deck-invariant brief the working ratio is ~3.5 characters
per token, so the brief's growth of 3 571 characters is roughly 1 000 input
tokens per call; the rest of the 5 440 → 12 700 gap is CONTENT and the blocks
section 2.3 names.

The fixture is one slide, held fixed for every number in this document:

```
brand   topic "2026 디지털 전환 전략", language Korean, primary #2563EB,
        fonts "Noto Sans KR", verbosity standard
slide   role PROBLEM, index 3 of 20, CONTENT "시장 현황과 세 가지 과제"
deck    template page numbers, no header contract, no chapter plan,
        no requirement identifiers, no style anchor, no illustration plan
```

The August baseline is `f994ba36b` (2026-08-19 23:26 KST), the last commit of
2026-08-19 and therefore the tree the reference deck was authored on. Its text
is read with `git show f994ba36b:servers/fastapi/utils/llm_calls/author_slide_rules.py`
and is reproduced in `utils/llm_calls/author_slide_brief_august.py`.

## 2. Inventory: every change to the authoring prompt family since 2026-08-19

### 2.1 What did NOT change

Measured by running the 2026-08-19 builders and today's side by side on the
same inputs, these are **byte-identical** and need no profile:

| block | chars |
|---|---|
| `structure_role_brief("SECTION")` / `("AGENDA")` | 489 / 380 |
| `requirement_marker` (the `data-requirement-ids` rules) | 1 090 |
| `chapter_marker` with and without a prefix | 326 / 248 |
| `illustration_marker` yes / none | 288 / 304 |
| `user_direction_brief` | 572 |
| `SYSTEM_PROMPT` | 158 |
| the three `PAGE_NUMBER_RULES_BY_MODE` texts (template / none / companyStandard) | 1 181 / 687 / 391 |

So the whole drift sits in the deck-invariant design system, the slide's own
line, the closing sentence, the **order** the blocks are stated in, and the
blocks invented since.

### 2.2 The design-system brief, change by change

`current` minus `august-0819`, on the fixture. A positive delta is text today's
prompt carries and August's did not.

| # | change | commit | date | Δ chars | profile |
|---|---|---|---|---|---|
| 1 | `SEMANTIC ROLES` vocabulary (`data-semantic-role`) | `8a003c969` | 2026-09-06 | +725 | **kept in both — contract** |
| 2 | `NEVER hide overflowing content` prohibition | `eb7a6309a` | 2026-09-07 | +374 | **kept in both — contract** |
| 3 | `SHARED UNIVERSALS` block (margins, one accent, eyebrow, vary-per-slide) | `147d388f1` | 2026-09-06 | +659 | restored (removed) |
| 4 | `DEFAULT_STYLE_BRIEF` trimmed to palette + fonts, universals lifted out | `147d388f1` | 2026-09-06 | −487 (818 → 331) | restored (August's 818-char brief returns) |
| 5 | inline-SVG enumeration compressed into `INLINE_SVG_CONTRACT` | `147d388f1` | 2026-09-06 | −541 (1 459 → 918) | restored (August's longer enumeration returns) |
| 6 | radius-family + gradient/shadow paragraphs | `147d388f1` | 2026-09-06 | +507 (218 → 725) | restored (removed) |
| 7 | content-box arithmetic, two paragraphs (`Width belongs to the CONTENT BOX`, `The same arithmetic applies down the page`) | `3c6108729` | 2026-09-03 | +645 | restored (removed) |
| 8 | `Connector clearance` paragraph | `2971728b6` | 2026-08-29 | +283 | restored (removed) |
| 9 | `No fragile positioning` rewritten to name the header band as fixed furniture | `147d388f1` | 2026-09-06 | +151 (277 → 428) | restored (August wording) |
| 10 | Korean/CJK line: `break-word`, 1.5–1.7 leading, numeral-and-unit | `147d388f1` | 2026-09-06 | +296 (139 → 435) | restored (August wording) |
| 11 | `Capability contract:` clause appended to the brief | `d78d5a307` | 2026-08-30 | +221 | restored (removed) |
| 12 | `DECK TOKENS` block appended to the brief | `147d388f1` | 2026-09-06 | +508 | restored (removed) |
| 13 | style exemplar slide appended to the brief | `147d388f1` | 2026-09-06 | +0 today | restored (removed) — **already empty**, `AUTHORED_EXEMPLAR_ENABLED` is off |
| 14 | `COMPOSITION INTENT` block | `8a003c969` | 2026-09-06 | +0 today | **already empty**, `AUTHORED_PROMPT_BUNDLE_ENABLED` is off |
| 15 | `TYPE SCALE` ramp replacing the per-verbosity floor | `147d388f1` | 2026-09-06 | +0 today | **already the pre-bundle floor**, same knob; the profile pins it so it cannot move |
| 16 | archetype clause on the slide's own line | `147d388f1` | 2026-09-06 | +50 | restored (removed) |
| 17 | closing sentence rewritten to state the `data-qid` rule | `3ec5d35e9` → `147d388f1` | 2026-08-22 → 09-06 | +187 | **kept in both — contract** |
| 18 | design system moved from **last** to **first** in the prompt (G8, prefix caching) | `147d388f1` | 2026-09-06 | 0 | restored (design system stated last) |

Measured end-to-end difference between the raw 2026-08-19 prompt and today's,
same fixture: **8 623 → 12 194 = +3 571**. The itemised deltas above are
paragraph sizes and sum to within ten characters of that, the difference being
separator newlines at the seams where blocks were inserted.

Item 18 costs no characters and may matter most: August stated the design
system **after** the slide's CONTENT and immediately before the output
contract, so the rules were the last thing read. Today they are the first, in
order to be a cacheable prefix (and, with `AUTHORED_PROMPT_SYSTEM_HOIST_ENABLED`
on by default, to be hoisted into the system message entirely). Recency is a
real variable in composition, so the profile restores the order and accepts the
cost — see section 6.

### 2.3 Per-slide blocks added since August, outside this profile

These are prompt text too, and they are the reason the receipt figure grew more
than the brief did. Each already has its own knob, and each is **left alone** by
this profile for the reason given.

| block | commit | date | size | why not in the profile |
|---|---|---|---|---|
| `DECK STYLE ANCHOR` (`author_style_anchor.prompt_block`) | `147d388f1` | 2026-09-06 | up to **6 400** chars per call, `AUTHORED_STYLE_ANCHOR_ENABLED` default **on** | the anchor **gate** (`services/authored_style_anchor_stage.py`) judges every slide against the anchor. Suppressing only the prompt block would judge slides against a contract they were never given. Set `AUTHORED_STYLE_ANCHOR_ENABLED=false` alongside the profile for a clean August run. |
| `VISUAL STORY PLAN` marker | `cc785a34d` | 2026-08-22 | 1 317 | `AUTHORED_VISUAL_STORY_PLAN_ENABLED`, default off |
| figure / infographic marker | `7c95334e1` | 2026-09-03 | varies | `AUTHORED_INFOGRAPHIC_PLAN_ENABLED`, default off |
| capacity brief | `046764c27` | 2026-08-28 | varies | `AUTHORED_COMPOSITION_ENVELOPE_ENABLED`, default off |
| placeholder contract | `c87aebd26` | 2026-09-11 | varies | `AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING`, default off |
| source-image brief | `e0a5c6267` | 2026-08-19 | varies | only present when the user attached images; unchanged in substance since 08-19 |

On the fixture (no anchor, every plan off) the profile therefore accounts for
the entire difference. On a real deck today the anchor is the larger single
line item.

## 3. The knobs

| key | type | default | choices |
|---|---|---|---|
| `AUTHORED_AUTHOR_PROMPT_PROFILE` | enum | `current` | `current`, `august-0819` |
| `AUTHORED_AUTHOR_PROMPT_MODEL` | string | unset | any model id; unset is **not** a pin |

Both are registered in `servers/fastapi/constants/settings_registry.json`
(group `authored.quality`, target `fastapi.runtime`) and read through
`utils/get_env_programme.py`
(`get_authored_author_prompt_profile`, `get_authored_author_prompt_model`).
The registry validates the enum on read, so a misspelled, differently-cased or
space-padded value raises before it reaches a builder — a silently wrong
profile would invalidate the comparison the knob exists for.

## 4. The profile table

`utils/llm_calls/author_prompt_profile.py` resolves the knobs into an immutable
`AuthorPromptProfile`, whose `sections` is one row of this table. Every row is
a **whole prompt**, never a paragraph.

| field | `current` | `august-0819` | consumed by |
|---|---|---|---|
| `design_system_rules` | `author_slide_rules.DESIGN_SYSTEM_RULES` (the live object, not a copy) | `AUGUST_DESIGN_SYSTEM_RULES` | `author_slide.build_design_system` |
| `default_style_brief` | `author_slide_rules.DEFAULT_STYLE_BRIEF` | `AUGUST_DEFAULT_STYLE_BRIEF` | same |
| `type_scales` | `None` → defers to `AUTHORED_PROMPT_BUNDLE_ENABLED` | `PRE_BUNDLE_TYPE_SCALES` | same |
| `deck_invariant_tail` | `True` (capability clause + deck tokens + exemplar) | `False` | `author_slide_prompt.build_design_system` |
| `archetype_clause` | `True` | `False` | `author_slide_prompt.author_prompt` |
| `design_system_last` | `False` | `True` | `author_prompt_profile.assemble_author_prompt` |
| `block_order` | anchor, direction, structure, chapter, requirement, illustration, visual_story, figure, capacity | structure, chapter, requirement, illustration, direction, anchor, visual_story, figure, capacity | same |
| `model` (on the profile, not the sections) | `None` | `None` | `author_slide` / `author_batch` model selection |

`None` for `type_scales` is the pattern that makes `current` byte-identical:
the row does not restate a decision another knob already owns.

August had no opinion about where the blocks invented after it should sit, so
the four of them (anchor, visual_story, figure, capacity) keep their current
relative position in the August order; the five blocks August did have are in
the order August stated them.

### Resolution, once per operation

`current_author_prompt_profile()` is a pure read of the request/task-scoped,
immutable `RuntimeSettings` snapshot (`utils/runtime_settings.py`), so two
reads inside one operation cannot disagree and a deck cannot author half its
slides under one prompt and half under another.
`author_prompt_profile_scope(profile)` pins an explicit profile for a caller
that resolved it itself, and a pinned profile outranks the ambient environment.

**Why this is its own module rather than a field on `RolloutProfile`.** The
task allowed either. `services/authored_rollout_profile.py` was being edited by
another agent while this landed (its `_require_coherent_switches` guard appeared
mid-change), and more substantively that profile's digest is the durable
`policy_revision` stamped into every admission receipt: adding a field would
change the digest and make every receipt minted before the deploy read back as
mismatched. The prompt profile is resolved from the same immutable snapshot, so
it has the same "one resolution per operation" property without touching a
durable digest. `services/authored_composition_envelope_profile.py` set the
same precedent five days earlier.

## 5. What `august-0819` restores, and what it cannot

### 5.1 Restores

1. **The August design-system brief**, items 3–12 of section 2.2: no shared
   universals block, August's own universals back inside the default style
   brief, August's full inline-SVG enumeration, August's one-line radius rule
   and two-line gradient rule, no content-box arithmetic, no connector
   clearance, August's `No fragile positioning` and Korean/CJK wording.
2. **No deck-invariant tail** — no capability clause, no deck-token block, no
   style exemplar.
3. **The August block order** — the slide's own line and its CONTENT first, the
   design system last, immediately before the output contract.
4. **No archetype clause** on the slide line.
5. **The pre-bundle type scale**, pinned: `AUTHORED_PROMPT_BUNDLE_ENABLED=true`
   alongside `august-0819` still yields the August floor. "The deck as August
   authored it" cannot be partly 2026-09.

### 5.2 Cannot restore — contracts that MUST stay in every profile

These are in `august-0819` verbatim from the current brief. Each is marked in
place in `author_slide_brief_august.py`.

| contract | why it cannot be dropped |
|---|---|
| `SEMANTIC ROLES` block (`data-semantic-role`) | the semantic-role repair pass and the capacity gate key off these values; a slide whose title owner is unmarked is blocked outright (`services/authored_composition_policy.py`, `services/authored_author_capacity_recovery.py`) |
| `NEVER hide overflowing content` (`-webkit-line-clamp`, `line-clamp`, `text-overflow: ellipsis`, `overflow: auto/scroll`) | `utils/llm_calls/author_slide_hidden_overflow.reject_hidden_overflow_declarations` runs at `author_slide.py:751`, before admission. A profile that did not state the rule would author slides the author stage then throws away, and would measure its own rejection rate |
| the `data-qid` sentence in the closing | `data-qid` is the sole edit-identity marker the projection, the repair passes and the hybrid export all key off. August's closing differed from today's only by a comma splice and this sentence, so there is no earlier version worth restoring: the closing is shared by both profiles (`author_slide_brief.SLIDE_OUTPUT_CONTRACT`) |
| the header contract (`{header_system}`) and `REQUIREMENT IDENTIFIERS` | both are byte-identical to August already (section 2.1); the header contract is also read back by the header repair pass |
| the renderer markup policy rules — meta allowlist, self-contained font policy, the SVG contract, the `text-shadow`/`filter`/`mix-blend-mode` prohibition | `utils/renderer_isolation.admit_authored_html` rejects violations. August's own wording already covers all of them, and where the two differ August is the stricter (it forbids percentage and per-corner radii outright), so restoring it cannot widen what the policy accepts |

### 5.3 Cannot restore — out of scope by construction

- **The style anchor.** Up to 6 400 characters per call, on by default since
  2026-09-06, and the single largest prompt addition on a real deck. Suppressing
  only its prompt block would leave the anchor **gate** judging slides against a
  contract they were never handed, so this profile deliberately does not
  override `AUTHORED_STYLE_ANCHOR_ENABLED`. A clean August comparison sets that
  knob to `false` as well.
- **The other per-slide blocks** in section 2.3. Each has a knob and each is
  already off by default.
- **The page-marker off-by-one.** August rendered `{index:02d}`, so the deck's
  second slide read `01`; `bb9f5a9f6` fixed it to `{index + 1:02d}`. That is a
  defect, not a density preference, and no profile restores it.
- **The illustration-slot gate.** Today the ~2 030-character slot contract is
  withheld when `AUTHORED_ILLUSTRATION_SHARE` is 0; August stated it always. The
  difference only fires on a deck that authors no illustration at all, so it is
  left as-is.
- **Deleted code.** Anything removed since 2026-08-19 is gone; the knob only
  chooses among code paths that still exist.
- **The composition envelope, the figure plan, the visual-story plan and the
  blocking DOM gate.** Those are
  `AUTHORED_COMPOSITION_ENVELOPE_PROFILE`'s subject, not this one's.
- **Any safety guarantee.** `august-0819` is an experiment profile. It is not
  the recommended configuration, and `current` stays the default.

### 5.4 The model

`AUTHORED_AUTHOR_PROMPT_MODEL` pins the authoring model so the prompt A/B is
run at a fixed one; it changes nothing else, and the tests pin that it does not
alter a single character of any prompt. It does **not** know what August's
default was — that is an operator input, not something the repository records.
The current default is `gpt-5.6-sol`. Without the pin, a prompt comparison is
confounded by whatever the provider's selection resolves to at each run.

## 6. Known consequences of `august-0819`

- **Prompt caching is lost.** With the design system stated last, the prompt no
  longer opens with the deck-invariant block, so
  `author_prompt_split.split_deck_invariant` returns `("", prompt)` and the
  system-message hoist degrades to sending the whole prompt in the user message.
  That is the *designed* failure mode of the hoist — it can only lose the
  optimisation, never lose text — and it is pinned by
  `test_august_states_the_design_system_last`. Expect a higher uncached input
  cost per call under this profile; that is the price of reproducing August's
  ordering.
- **`AUTHORED_PROMPT_CACHE_KEY_ENABLED` still names a lane**, but with no shared
  prefix the lane has nothing to reuse.

## 7. Verification

`servers/fastapi/tests/unit/test_author_prompt_profiles.py`, 45 tests.

**`current` is the prompt it replaced.**
`test_current_sections_are_the_live_constants` asserts the row holds the *same
objects* as `author_slide_rules`, so no copy can drift from its source.
`test_current_defers_the_type_scale_to_the_knob_that_already_decided_it` covers
all four verbosities. `test_current_reassembles_the_prompt_exactly_as_it_was_written_before`
recomputes the pre-change assembly formula in the test and compares byte for
byte — a stored whole-prompt snapshot would fail on any later legitimate edit
to the brief, whereas this pins the shape, which is what the profile changes.
`test_current_keeps_the_deck_invariant_tail_and_the_hoistable_prefix` pins that
the hoist still fires. Out of tree, the rendered default-style prompt was
compared against a copy taken before any edit: **identical, 12 194 characters**.

**`august-0819` is the August text.**
`test_august_states_the_august_brief_and_style_brief`,
`test_august_drops_the_composition_guidance_added_since` (7 sentences),
`test_august_states_the_design_system_last`,
`test_august_names_the_role_without_an_archetype`,
`test_august_drops_the_deck_invariant_tail`,
`test_august_pins_the_pre_bundle_type_scale_over_the_bundle_knob`.

**Contracts hold under both.**
`test_every_profile_states_every_required_contract` runs 10 markers across both
profiles; `test_the_two_contract_insertions_are_in_the_august_brief` and
`test_the_requirement_and_chapter_markers_are_profile_invariant`.

**Resolution.** `test_the_knob_resolves_to_a_profile`,
`test_a_value_outside_the_two_choices_is_refused_not_rounded` (5 values,
including `"august"` and `" august-0819"`),
`test_an_unknown_profile_name_is_a_coded_rejection`,
`test_a_pinned_profile_outranks_the_ambient_environment`,
`test_the_model_pin_is_unset_by_default_and_pins_when_set`,
`test_the_model_pin_does_not_change_a_single_character_of_the_prompt`.

**The numbers above.** `test_the_recorded_character_counts_still_hold` asserts
11 569 / 12 194 for `current` and 9 335 / 9 910 for `august-0819` on the
fixture, so this document's table cannot silently rot.

Unchanged and still passing: `test_author_prompt_cache_and_hoist.py`,
`test_author_slide_brief.py`, `test_authored_header_contract.py`,
`test_authored_page_numbering.py`, `test_authored_source_image_prompt.py`,
`test_authored_illustrations.py`, `test_authored_illustration_tier_rules.py`
(151 tests).

## 8. The size table, for the record

| fixture | `current` brief | `current` prompt | `august-0819` brief | `august-0819` prompt | Δ prompt |
|---|---|---|---|---|---|
| default style | 11 569 | 12 194 | 9 335 | 9 910 | −2 284 (−18.7 %) |
| academic-edge | 13 171 | 13 796 | 10 450 | 11 025 | −2 771 (−20.1 %) |
| architectural-portfolio | 14 104 | 14 729 | 11 383 | 11 958 | −2 771 (−18.8 %) |
| broadside | 14 746 | 15 371 | 12 025 | 12 600 | −2 771 (−18.0 %) |
| devax-si-proposal | 18 599 | 19 224 | 15 878 | 16 453 | −2 771 (−14.4 %) |

The default style saves less than a preset because August's own
`DEFAULT_STYLE_BRIEF` is 487 characters longer — it still carried the universals
a preset never received, which is the gap `147d388f1` set out to close.

For reference, the **raw** 2026-08-19 prompt on the default fixture is 8 623
characters. `august-0819` is 9 910, and the 1 287-character difference is
exactly the three contracts of section 5.2: +726 for the semantic-role block
and its separator, +374 for the hidden-overflow paragraph, +187 for the
`data-qid` sentence. That is the complete, auditable price of making the August
prompt runnable against today's gates.
