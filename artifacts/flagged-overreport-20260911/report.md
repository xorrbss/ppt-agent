# The review flag over-reports: 60% of flagged slides ship clean

Date: 2026-09-11. Investigation only — no code change, no generation, no provider call,
no database write. Four published runs of the verbatim 35-slide fixture
(`artifacts/authored-full-product-validation-20260910` runs 11, 15, 16, 20).

## 1. Every flag has the same reason

53 flagged slides across the four decks. The top-level reason is uniform:

| field | value | count |
|---|---|---|
| `reason` | `composition_safety:gate_repair_not_admitted` | 53 / 53 |
| `remakeAttempted` | `false` | 53 / 53 |
| `remakeOutcome` | `verbatim_flag_only` | 53 / 53 |

`reviewPages` mirrors `flaggedSlides` one-for-one in every run, with the same reason
string, so what a reviewer is shown is exactly this list.

The only differentiation is a sub-list on 9 of the 53 entries, which additionally carry
`reasons: [gate_repair_not_admitted, source_visibility]` and a
`sourceVisibilityBlockIds` list (run-11 ×1, run-15 ×4, run-16 ×3, run-20 ×1). Those are
merged later, after the browser source readback
(`services/authored_presentation_generation.py:2916`). **The other 44 entries carry no
sub-reason at all.**

## 2. Most flagged slides carry no defect on the bytes that shipped

Each flagged slide's stored `slides.html_content` was rendered once (fit disabled) and
judged by the pipeline's own gate (`inspect_authored_dom` + `blocking_safety_issues`).
Script `flagged_before_only.py`; per-slide records `flagged-blocking-by-slide.json`
(runs 11/15/16 reuse the renders from
`artifacts/local-fit-source-reflow-replay-20260911`).

| run | flagged | any blocking defect | geometry defect | clean | `empty_media_panel` only |
|---|---|---|---|---|---|
| run-11 | 7 | 4 | 2 | 3 | 2 |
| run-15 | 17 | 4 | 3 | 13 | 1 |
| run-16 | 11 | 5 | 4 | 6 | 1 |
| run-20 | 18 | 8 | 4 | 10 | 4 |
| **total** | **53** | **21** | **13** | **32** | **8** |

- **32 of 53 (60%) have no blocking defect of any kind** on the published bytes.
- **40 of 53 (75%) have no geometry defect** — the 8 `empty_media_panel` slides are an
  image-panel content problem, not something a geometry gate or fitter can repair.
- Only **13 of 53 (25%)** carry the kind of defect the flag's `composition_safety:`
  prefix implies.

## 3. Why generation-time state survives into the published receipt

The flag is created by `declare_unadmitted_gate_repair(index)`
(`services/authored_terminal_slide_policy.py:127`), called from the bounded repair round
(`authored_presentation_generation.py:2248`, `:2292`, `:2300`, `:2622`) when a repair
candidate's `final_status != "admitted"`. Its docstring is explicit about what it records:
*"a slide whose one bounded gate repair produced a candidate that could not be admitted"*.
That is a **process outcome**, not a statement about the slide that ships — the original
slide is what publishes, and the flag rides along with it.

The deck is then re-measured. `_final_authored_quality_gate` produces
`residual_gate_failures` over the final bytes, and at `:2838`:

```python
if residual_gate_failures and flagged_slides:
    residual_gate_failures, flagged_residual_failures = (
        partition_residual_failures(residual_gate_failures, flagged_slides)
    )
```

`partition_residual_failures` (`authored_terminal_slide_policy.py:251`) splits residuals
into *blocking* (fails the deck) and *accepted because this slide is flagged*. **The flow
is one-directional: residuals are classified by the flag, and no path clears a flag when
the final gate finds that slide clean.** A flag, once declared, is permanent for the
attempt; the final measurement can only be excused by it, never contradict it.

The repair round's input is measured earlier, on pre-repair bytes. Between that
measurement and publication the deck passes through deterministic repairs, local fit,
role canonicalisation and a fresh render — any of which can resolve the defect that
triggered the flag. Nothing re-reads the flag afterwards.

**Stated as a limit:** this is the code path plus the outcome. I did not trace an
individual slide from its generation-time `gate_failures` entry to its clean final bytes,
because the per-slide generation-time gate evidence is not in the published receipt for
these runs. That a defect was resolved later, rather than never having been there in the
final render, is inference.

## 4. Two prescriptions

**A. Clear a flag the final gate contradicts.** At the existing partition site, drop a
flag whose index has no residual issue.

- *Safe by construction on the obvious risk*: only flags with **no** residual would be
  cleared, so nothing moves from "accepted as flagged" to "blocking" and no deck starts
  failing that publishes today.
- *Risk*: the flag is also the record that a repair budget was spent on that slide. Clear
  it and the audit trail goes with it — keep the entry under a separate receipt field
  (`repairHistory`) rather than deleting it.
- *Risk*: the 9 `source_visibility` entries are merged **after** this point (`:2916`).
  Clearing must run after that merge, or it drops visibility flags that the readback
  contract requires.

**B. Split the reason, keep the entry.** Distinguish `review_required` (the final gate
still shows a defect on the published bytes) from `repair_not_admitted` (a repair attempt
that was not admitted, with nothing measured on what shipped), and derive `reviewPages`
from the first only.

- *Risk*: the vocabulary has consumers —
  `services/authored_generation_timing_receipt_e4.flagged_slide_entries`,
  `reviewPages`, and any UI reading `flaggedSlides[].reason`. Each needs the new values.
- Nothing is removed, so the evidence and the audit trail both survive. This is the
  lower-risk of the two, and it produces A's user-visible effect through `reviewPages`.

**Recommendation: B.** The problem is that one label carries two meanings, and the fix
that addresses that directly is to stop conflating them — not to delete one of them.

Either way the reviewer-facing count on this fixture drops from 53 to 21 (or to 13 once
`empty_media_panel` is routed to the generation track, which owns it).
