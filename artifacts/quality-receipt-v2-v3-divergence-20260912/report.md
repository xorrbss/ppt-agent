# Two quality receipts, one deck, opposite verdicts

Found 2026-09-12 while examining the receipt of a failed live-series run, offline,
from `artifacts/authored-live-series-20260915/normalization-tree/run-1/06-final-status.json`.
No provider call and no stack were needed to establish any of this.

## What the receipts say

One generation attempt produces two receipts. They disagree.

| | v2 (`data.quality_gate_receipt`) | v3 (`.qualityReceiptV3`) |
| --- | --- | --- |
| verdict | `status: passed`, `gatePassed: true` | `qualityStatus: blocked`, `gatePassed: false` |
| blockers | `[]` | `["visualReview.failed"]` |
| tier | — | `T1` |
| triggers | — | 43 |

The v2 receipt on the same deck also records `visualReviewStatus: "not_assessed"` and
`requireVisualReview: false`. The deck was not required to have a visual review, did not
get one, and v2 treats that as fine.

## Why they disagree

The two gates read the same absence and reach opposite conclusions.

**v2** (`services/authored_quality_receipt_v2.py:513-516`) is explicit about the
not-required case:

```python
for key, required in (("visualReview", require_visual_review), ("performance", require_performance)):
    if statuses[key] in {"pending", "unknown"} and not required:
        statuses[key] = "not_assessed"
    if statuses[key] in {"failed", "blocked"} or (required and statuses[key] in {"pending", "not_assessed", "not_applicable"}):
        _append(blockers, key + (".failed" if statuses[key] in {"failed", "blocked"} else ".unverified"))
```

A blocker needs either a real failure, or a status that is missing *while the check was
required*. Not required plus not assessed produces nothing. That is the correct reading.

**v3** (`services/authored_quality_receipt_v2.py:2114-2115`) drops the requirement term:

```python
if quality_tier in {"T1", "T2", "T3"} and (visual_status := ...) != "passed":
    _append(blockers, "visualReview.failed" if visual_status == "failed" else "visualReview.required_failed")
```

Any tier at or above T1 must have a *passing* visual review. Whether one was required is
not consulted. So for every deck that reaches T1, "we did not run this optional check"
becomes a blocker.

## The amplifier: one provider recovery per slide is enough to reach T1

`quality_receipt_t1_triggers` (`:1774-1781`) emits one trigger per slide whose provider
call needed any recovery at all:

```python
recovery = call.get("recovery", 0)
if isinstance(recovery, int) and not isinstance(recovery, bool) and recovery > 0:
    _append(triggers, f"provider:slide:{slide_index}:recovery")
```

On this deck that fired for **35 of 35 slides**, alongside 2 boundary and 6 repair
triggers. A single retry on a slide is indistinguishable from a quality signal here.

The chain is therefore: any provider recovery anywhere in the deck → T1 → v3 demands a
passing visual review → the optional review was never run → blocked.

## Impact today: latent, not live

Publication is gated on v2 alone. `services/authored_presentation_generation.py:3118`:

```python
if not quality_receipt_v2.gate_passed:
```

There is no equivalent consumer of the v3 verdict on the generation path, so a v3
`blocked` does not stop a deck today.

It is not inert, though. The v3 receipt is persisted and projected outward
(`services/public_quality_receipt_v3_projection.py`, `services/public_quality_receipt_v3_store.py`),
so anything reading v3 sees "blocked" for a deck that v2 published. And if v3 ever
becomes authoritative, essentially every deck that experienced a single provider retry
would be blocked on an optional check it was never asked to run.

## What to decide

1. **Should v3 honour the requirement flag?** If the answer is yes, the fix is to carry
   `require_visual_review` into the v3 evaluation rather than to special-case the tier
   test. The two gates should not be able to disagree about the same evidence.
2. **Is one provider recovery the right T1 threshold?** The trigger's own docstring says
   a caller must not be able to downgrade a risk-bearing slide by omitting a flag, which
   argues for sensitivity. But at one-retry granularity the signal saturates: on this run
   it selected every slide, which is the same as selecting none.
3. **Which receipt is authoritative?** Right now v2 decides and v3 is published. That is
   the most confusing of the three possible arrangements.

## Related

The 35/35 recovery rate is the same phenomenon measured from another angle in
`artifacts/authored-speed-decomposition-20260912/report.md`, where effective authoring
concurrency is measured at 4.2 against August's 7.6.
