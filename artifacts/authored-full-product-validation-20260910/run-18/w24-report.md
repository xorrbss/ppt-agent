# W24 — run-18 void end-tag author failure

## Outcome

Run 18 failed on zero-based slide index 30 (slide 31) after two provider calls because authored intake passed a browser-ignored `</img>` end-tag token to the strict renderer policy. The fix is at authored-HTML intake: it deletes only genuine end-tag tokens for HTML void elements before renderer admission, records `html_void_end_tag_dropped` with tag and count, and leaves `renderer_isolation.py` unchanged and fail-closed for every non-void imbalance.

Implementation commit: `9beeb349d` (`Normalize authored void element end tags`).

## Persisted evidence and raw-output limit

The sanctioned `Read-StackEnvironment` wrapper pattern was used to inject the running stack's worker environment into an in-process SQLAlchemy probe; no environment value or secret was printed. The transaction was set read-only before querying.

The persisted `authored_slide_checkpoints` row confirms `author_status=failed`, `failed_stage=author`, `provider_attempt_count=2`, and cause `HTML closing tag is unbalanced: img`. It has no raw candidate: `author_html` and `illustrated_html` are null and `artifact_refs` is empty. The job's provider receipts are intentionally scalar-only accounting records (35 initial author receipts and 5 recovery author receipts across the deck), not request/response storage. A bounded filesystem search for the operation/job identifiers found only `app_data/logs/worker-generation-20260911-145547.stderr.log`, which likewise records the terminal cause without HTML. Therefore the persisted cause proves `img` was an offending end-tag name, but the raw occurrence count and any later offending tags cannot be recovered.

Minimal reproduction:

```html
<html><head></head><body><img data-qid="hero"></img></body></html>
```

Direct pre-normalization admission reproduces `RendererPolicyError: HTML closing tag is unbalanced: img`. The new intake output removes only `</img>`, admits the remaining HTML, and records `{"rule":"html_void_end_tag_dropped","detail":"img","count":1}`.

## Retry feedback

The second author call did not receive the exact run-18 rejection. `_batch_failure` classified an otherwise-unrecognized `RendererPolicyError` as generic `renderer_policy` guidance, so the recovery prompt said to satisfy the reported rejection without including what that rejection was. All renderer-policy retry corrections now prepend the exact whitespace-collapsed rejection, bounded to 240 characters and explicitly labeled diagnostic data rather than instructions. The existing two-call budget and rejection policies are unchanged.

## Implementation

- `utils/authored_void_end_tag_normalize.py` uses `HTMLParser` only to identify real void-element end-tag tokens, then deletes exact source spans without DOM reserialization. Comments, raw style text, and unrelated bytes are untouched.
- `utils/authored_html_normalize.py` runs the rule before the existing structure-sensitive normalizers, allowing those later rules to inspect the browser-equivalent structure.
- Single-slide and three-slide batch author paths pass per-slide normalization receipts through to the pipeline's slide quality evidence.
- `renderer_isolation.py` was not changed. A stray non-void `</span>` remains untouched by normalization and is still rejected by renderer admission.
- `services/authored_presentation_generation.py` was not changed and remains 3,468 lines, below its 3,473-line cap.

## Offline verification

- Targeted pytest: 6 passed in 6.88 seconds. This covers void end-tag removal, non-void fail-closed admission, author-intake receipt recording, batch receipt recording, exact bounded retry feedback, and slide-quality receipt propagation.
- Requested three-file pytest run: 190 passed and 6 failed in 70.02 seconds. The failures are pre-existing/unrelated expectations in `test_authored_pipeline.py` (vision reasoning effort, two service fixtures, one timing-sensitive streaming test, the 90-slide render mock expectation, and a PPTX width expectation); none is in the W24 tests.
- Ruff across every touched Python file encountered the file's existing 239 `F403`/`F405` findings from `authored_presentation_pipeline.py`'s wildcard dependency facade. Re-running the same touched-file set while ignoring only those two established facade findings passed with no other Ruff issue.
- `npm run test:file-size-ratchet` failed only on ten unrelated pre-existing/growing files and did not name any W24-touched file. The oversized Python files touched by W24 did not grow relative to HEAD.
- `git diff --cached --check` passed before the implementation commit.

No local service was restarted or otherwise touched. End-to-end product-path confirmation requires the coordinator to schedule a supervised stack restart and a fresh generation run; this worker performed offline verification only, as required.
