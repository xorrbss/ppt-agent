# AI-authored page remake design

## Decision

`페이지 다시 만들기` is a separate, explicit one-page action. It is not a
quality-review mode and it is never called by an all-slide or subset review.
Quality review may report an unresolved problem and link to the action, but it
cannot start page generation itself.

## User flow

1. The header exposes `페이지 다시 만들기` for the current AI-authored page.
2. Opening the action identifies the exact one-based page and explains that the
   live page is unchanged while a candidate is generated.
3. `미리보기 만들기` starts one bounded LLM full-page re-author operation for
   the exact zero-based slide index.
4. The server renders and validates the detached candidate. A candidate that
   cannot be generated or rendered remains unpublished and returns a stable
   stage/reason code.
5. A rendered candidate is shown as a 16:9 preview with source/candidate digests
   and validation state. Deterministic DOM findings are advisory: they are
   reported as a warning next to the preview, and the user decides.
6. `이 버전 적용` performs a source-digest CAS, snapshots the deck, removes a
   stale SlideSpec active pointer, publishes the candidate image and HTML, and
   verifies the committed representation. `취소` leaves the live deck intact.

The button label is exactly `페이지 다시 만들기`. Wording containing `AI로`
or implying that review automatically regenerates a page is not used.

## API boundary

- `POST /presentation/{presentation_id}/slides/{slide_index}/remake/preview`
  creates a detached asynchronous preview task.
- `POST /presentation/{presentation_id}/slides/{slide_index}/remake/publish`
  accepts only the completed preview task id produced for the same presentation,
  slide id, slide index, owner, and source digest.
- The existing `/quality-review` contract is unchanged and has no remake flag.

Both routes use a path-level single slide index. There is no `all`, `subset`, or
array form, making a deck-wide LLM remake structurally unavailable through this
feature.

## Safety contract

- The preview is detached: generation and render failures cannot mutate a live
  slide.
- The candidate preserves the saved semantic outline, role, brand, style, slide
  number, and total deck size.
- Exact-reconstruction decks fail closed unless their immutable source snapshot
  can be verified and supplied to the author.
- Candidate HTML must be valid and render successfully before it can be offered
  for publication. Local DOM quality inspection is advisory: its findings are
  surfaced as `validation.candidate_dom = warning` with the issue list and probe
  coverage (`validation.candidate_dom_telemetry`), never as a silent rejection.
  Publication re-renders the candidate — a render failure still blocks — and
  records the same advisory verdict as `post_verify`.
- Publication compares the current slide id, HTML, content, properties, and
  source digest with the preview source. A mismatch returns a conflict and does
  not publish.
- The pre-change deck is snapshotted before the CAS update. The snapshot remains
  the rollback path.
- A regenerated HTML page invalidates its prior `slidespec_active` pointer so the
  next quality review imports and measures the new source rather than stale
  evidence.
- Kill switches and the legacy/chat-first write gate remain authoritative.

## Result states

`idle -> generating -> preview_ready -> publishing -> published`

Terminal failures retain their stage and stable reason:

- `source_validation_failed`
- `generation_failed` (retryable; also covers an attempt the reviser aborted)
- `candidate_invalid` (no candidate HTML or PNG was produced at all)
- `candidate_render_failed`
- `source_changed`
- `publish_cas_conflict`
- `post_verify_failed` (publication re-render failure only)

Candidate DOM quality is not a terminal state. A renderable candidate always
reaches `preview_ready` with `validation.candidate_dom` of `passed` or
`warning`, and a published page records `post_verify` of `passed` or `warning`
together with its issue summary.

Neither is an identical result. Re-authoring a page that was already remade can
legitimately return the same HTML, so the outcome is reported as
`validation.candidate_change` of `changed` or `unchanged` and the candidate is
still shown for the user to judge. `revise_authored_deck` swallows model and
transport failures and hands the source back unchanged, so that case is
separated by the attempt's own outcome (`attempted` without `succeeded`, or no
reported change) and raised as the retryable `generation_failed` rather than
being misreported as a defective candidate.

The UI shows generation and publication as distinct states. Closing the dialog
or rejecting a preview never reports the page as fixed.

## Verification

- Contract tests reject negative/out-of-range slide indices, cross-slide task
  reuse, cross-presentation task reuse, stale source digests, and incomplete
  preview tasks.
- Service tests prove preview is non-mutating, publish snapshots once, CAS
  conflicts preserve the original, and stale SlideSpec pointers are removed only
  when the candidate is committed.
- Service tests also prove a DOM-warned candidate still completes as a preview
  with its issue evidence, leaves the live slide untouched, and publishes only
  after an explicit user approval.
- Component tests prove the exact button label, current-page request, detached
  preview, explicit publish, cancellation, and reload behavior.
- Production build and focused browser/component regressions are required before
  release.
