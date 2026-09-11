# T4 provider overload and quota-pause implementation

## Result

The authored provider boundary now classifies only the accepted overload set (HTTP 429, 500, 502, 503, 504 and call timeout), owns one bounded idempotent physical replay, reserves that replay durably without consuming a content ordinal, and turns an explicit `usage_limit_reached` response with a reset timestamp into a deck-level durable pause. Generic bridge exceptions are not overloads. The superseding decision records three content ordinals per operation attempt, with ordinal three reserved for the final simplified author attempt.

Implementation commit: recorded in the Orca `worker_done` message after this report was written.

## Main changes

- `utils/provider_overload.py:47` defines the coded `ProviderQuotaPause`; `utils/provider_overload.py:66` bounds physical calls, applies the existing 30-second cooldown schedule, requires an idempotent transport and a retry reservation, shortens each physical transport timeout to the remaining deadline, and exposes a content-free receipt.
- `utils/provider_idempotency.py:244` bridges the async checkpoint reservation into the blocking provider thread; `utils/provider_idempotency.py:276` owns status/timeout/quota classification and pins hidden SDK retries to zero.
- `services/authored_provider_attempt_ledger.py:101` preserves three content ordinals and reserves the first physical request; `services/authored_provider_attempt_ledger.py:190` records the overload replay separately as physical ordinal two while retaining the content ordinal.
- `services/authored_batch_policy.py` retains the sole shared overload predicate, now restricted to 429/500/502/503/504, timeout, and provider-layer coded overloads; `utils/llm_calls/author_slide_admission.py:71` consumes provider-layer overload metadata rather than reclassifying raw exceptions.
- `services/authored_wave_scheduler.py:152` and `:196` propagate quota pauses before per-slide recording.
- `.spec-memory/decisions/authored.provider-attempt-budget-per-operation-attempt.2.json` supersedes revision 1 with the accepted three-ordinal and separately-accounted physical-retry contract.

## Authorized seam edits

- `services/authored_author_stage_deferral.py:127-134`: returns `ProviderQuotaPause` unchanged so the existing `WorkspaceOperationDeferred` runner path queues the deck without charging a retry.
- `services/authored_author_stage_rescue.py:268-289`: removes W26's caller-side overload classification/cooldown/replay; ordinal three now calls once and relies on the provider layer for any physical replay. The rescue readback/flag behavior was not changed by T4.
- `services/workspace_operation_runner.py:260-261`: after coordinator authorization, adds exactly the two content-free quota receipt fields to the central event sanitizer so the reset boundary and resume semantics survive durable deferral.

## Verification

- Focused behavior suite: `uv run pytest tests/unit/test_authored_provider_outcomes.py tests/unit/test_authored_batch_policy.py tests/unit/test_authored_provider_rate_limit.py tests/unit/test_authored_wave_scheduler.py tests/unit/test_authored_author_stage_rescue.py tests/unit/test_authored_generation_authority.py::test_a_later_operation_attempt_gives_a_failed_slide_its_calls_back` — **100 passed**.
- Ruff on every T4-touched Python file (including the authorized seams and tests) — **passed**.
- `npm run test:file-size-ratchet` — **failed only on unrelated concurrent/pre-existing files**: `authored_local_fit_evidence.py`, `authored_presentation_publication.py`, `presentation_export_direct_attempt_service.py`, `public_export_operation.py`, `workspace_artifact_publication_service.py`, `WorkspaceOperationCard.tsx`, `presentation-generation.ts`, `PresentonMode.tsx`, `authored-hybrid/export.ts`, and `authored-hybrid/slide-layer.ts`. No T4 file was reported; T4 source modules remain below 500 lines except the pre-existing central workspace runner seam.
- The Run-22 `usage_limit_reached` log shape is a committed fixture. Tests cover its reset parsing, provider receipt, scheduler propagation, operation state persistence, sanitizer allowlist, durable retry reservation, deadline cutoff, and unsupported-transport single-call behavior.

## Remaining live verification

No live provider or stack run was performed, as required while the account is rate-limited through 2026-09-15. After the recorded reset, the coordinator should requeue/resume the operation and verify a real provider retry receipt plus the queued-to-running transition; this is the only remaining product-path evidence.
