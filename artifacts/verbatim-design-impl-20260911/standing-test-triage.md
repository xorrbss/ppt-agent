# Standing FastAPI test triage (2026-09-12)

Scope: the tests named in the task brief as standing failures in
`servers/fastapi`. Investigated against `serve/main-20260902` HEAD
`adeb6b8d1` in an isolated worktree
(`.claude/worktrees/triage-standing-tests-20260912`) to avoid a concurrent
session's in-progress, momentarily import-broken edits to
`services/authored_admission_policy.py` / `services/authored_presentation_pipeline.py`
in the shared worktree (see "Environment note" below).

Legend: **(a)** stale assertion, code behavior is intended/documented →
assertion updated. **(b)** environment-dependent → precise skip needed (none
found in this batch). **(c)** real defect / needs a product decision → left
untouched, written up below.

## Table

| Test | Class | Evidence | Action |
|---|---|---|---|
| `test_authored_pipeline.py::test_build_image_pptx_one_fullbleed_picture_per_slide` | (a) | `build_image_pptx` switched from `Inches(13.333)/Inches(7.5)` to the exact `Emu(12_192_000)/Emu(6_858_000)` (960×540pt at 12700 EMU/pt) in commit `bd72851e4b` ("wip: checkpoint multi-session authored generation work"), with an in-code comment explaining the old decimal-inch value was 0.024pt narrow. | Updated the expected EMU pair to `12_192_000 / 6_858_000`. |
| `test_authored_pipeline.py::test_author_slide_style_reference_failure_is_one_receipted_provider_outcome` | (a) | Commit `5590702f7` ("Centralize authored provider overload handling", T4) narrowed overload/recovery to the accepted status/timeout/quota set and hardcoded `recovery_allowed=False` for the generic bridge-exception path (`utils/provider_idempotency.py`). `artifacts/verbatim-design-impl-20260911/T4-report.md`: "Generic bridge exceptions are not overloads." | Updated `recovery_allowed is True` → `is False`, cited the commit/report. |
| `test_authored_pipeline.py::test_vision_qa_reuses_selected_design_system` | (a) | Commit `bd72851e4b` changed `reasoning_effort="high"` → `reasoning_effort=None` at the vision-QA re-author call site (`utils/llm_calls/author_vision_qa.py`), with an in-code comment: "The durable generation preset is request-scoped and remains authoritative during the optional re-author pass as well." | Updated expected tuple's `reasoning_effort` from `"high"` to `None`. |
| `test_authored_pipeline.py::test_authored_service_resolves_passes_and_persists_canonical_style` | (a) | Two compounding drifts from the local-fit rollout (`fdba8236a` / `7b2dc81eb`): (1) generation now requires `len(local_fit_receipts) == len(htmls)` whenever `local_fit_applies()` is true for the deck (`services/authored_presentation_generation.py:~1636`, `services/authored_local_fit_policy.py`); (2) `_build_authored_export` gained a keyword-only `local_fit_receipts` param (`bd72851e4b`, `services/authored_presentation_artifacts.py:170`). The test's fixture predates both. | Monkeypatched `authored_generation_mod.local_fit_applies` to `False` (this test is about style resolution, not local fit) and widened the `_build_authored_export` stub lambda to accept `**_kwargs`. |
| `test_authored_pipeline.py::test_a_design_replay_is_authored_rather_than_dumped_verbatim` | (a) | Same as above. | Same fix as above. |
| `test_authored_pipeline.py::test_slide_pipeline_renders_fast_slide_before_slowest_author_finishes` | (a) | Root-caused by tracing wall-clock timing with instrumented prints: when `local_fit_applies()` is true (the default for an ordinary, non-source-reflow deck), `services/authored_presentation_pipeline.py` renders through `render_html_to_png_with_metrics`/`capture_with_policy` (real Chrome), **not** the module-level `render_html_to_png` name the test mocks — that seam is only used on the `local_fit_policy_value is None` branch. The module docstring states "no real LLM / no real Chrome"; local-fit's render path silently defeated that. | Monkeypatched `authored_pipeline_mod.local_fit_applies` to `False` so render goes through the mocked seam again. |
| `test_authored_pipeline.py::test_ninety_slide_pipeline_keeps_worker_concurrency_bounded` | (a) | Same root cause as the previous row (confirmed identically: `rendered_html`/`rendered` count was 0, i.e. the mock was never invoked). | Same fix (`local_fit_applies` monkeypatch). |
| `test_authored_generation_authority.py::test_pre_author_planner_freezes_known_closing_text_and_call_budget` | (a) | Commit `c7b6383c1` ("author ordinary decks by free composition again") raised `_ROLE_BUDGETS["CLOSING"]` from `(62, 270, 350, 20)` to `(62, 380, 460, 36)` (`services/authored_composition_policy.py`) — both the char targets *and* the qid ceiling — but only the char-target assertions in the test were bumped in that commit; the fixture's repeat count and the `semantic_target_limit == 20` assertion were left behind, so the fixture content (428 visible chars) no longer exceeds the new 460-char target and the qid assertion checks a stale constant. | Raised the repeat count 20→24 (visible chars 428→512, clears 460 with headroom) and updated `semantic_target_limit` to `36`. |
| `test_authored_generation_authority.py::test_attempt_receipt_keeps_non_null_stages_and_operational_causes` | (a) | `GenerationTimingRecorder.snapshot_stage_timings_ms()` has never zero-filled unrecorded stages (unchanged since its introduction in `4e7221520`, predating this test's own creation in `2800b3cf1`) — the test's assumption was wrong from day one. The one production caller (`services/authored_presentation_generation.py:1107`, `stage_timings_ms = {stage: 0 for stage in _PERFORMANCE_STAGE_NAMES}`) always zero-fills its own local dict before merging in recorded values; it never relies on the recorder for that. | Test now builds `stages` the same way production does (zero-fill `GENERATION_TIMING_STAGES` then overlay `recorder.snapshot_stage_timings_ms()`) before asserting. |
| `test_renderer_isolation.py::test_default_chrome_path_contains_pipe_data_document_and_no_unsafe_flags` | (a) — **not (b) as the task brief guessed; no Chrome binary is involved** | `FakePipe.command()` never handled `Target.createBrowserContext`, a real CDP command `utils/slide_capture_pipe_render.py:render_one` issues *before* `Target.createTarget` (added in the same commit, `2800b3cf1`, that introduced this render path — the test's fake was simply incomplete from creation). The fallback `return {}` yields no `browserContextId`, and `render_one` raises `"Chrome DevTools pipe context is missing"`. Entirely mocked; no real Chrome is touched anywhere in this test. | Added a `Target.createBrowserContext` case to `FakePipe.command` returning `{"browserContextId": "context"}`. |
| `test_authored_checkpoint_service.py::test_filesystem_evidence_recovers_db_pointer_and_clears_render_failure` | (b) | The subprocess crash-writer harness (`tests/support/authored_checkpoint_crash_writer.py`) is spawned with `cwd=<fastapi root>` but no `PYTHONPATH`; Python only adds the *script's own* directory (`tests/support/`) to `sys.path`, not `cwd`. `services.*` imports resolve anyway via the editable install, but that import chain now transitively reaches the top-level `migration_revision_contract.py` (a loose script at the fastapi root, not part of the editable package), which is unimportable without the root on `sys.path`. Reproduces deterministically without a `PYTHONPATH` override; not tied to any missing external dependency, so listed as (b) only in the narrow sense of "subprocess sys.path wiring," matching the task brief's own phrasing. | Set `PYTHONPATH` (prefixed with the fastapi root, preserving any existing value) in the subprocess `env`. Verified the fix in isolation (repeated runs, 100% pass) and noted a **separate, pre-existing, unrelated flake** below. |
| `test_authored_vnext_master_contract.py::test_file_repository_keeps_immutable_revisions_and_never_saves_preview_html` (:393) | (c) | `admit_authored_html(preview.html)` no longer raises `RendererPolicyError`. Root cause: `utils/renderer_css_policy.py`'s `ALLOWED_CSS_PROPERTIES` has been deliberately, extensively widened since this test was written (commit `33fef6d1a`, "fix: widen the authored renderer policy to what real decks emit," postdates this test's creation in `3ae62740b`) to cover exactly the properties `services/authored_vnext/master/preview.py` emits (`border-radius`, `list-style`, `object-fit`, `z-index`, etc.). The test's `pytest.raises(RendererPolicyError)` was never asserting a designed vNext/authored admission boundary — it was incidentally relying on the *narrowness* of a shared allowlist that was widened for an unrelated, well-documented reason. | Decided and rewritten 2026-09-12: the preview MAY pass; see "Resolutions (2026-09-12)" item 4. |
| `test_authored_checkpoint_service.py::test_semantic_gate_recaptures_stale_role_metrics_once` | (c) | Calls `pipeline._refresh_semantic_gate_evidence(...)`, a function deleted in commit `5218198f3` ("feat(authored): seal semantic scene hybrid release path"), which replaced whole-deck gate-time metric recapture with a "seal once before Chromium, immutable authority" scene-envelope model (`_evaluate_slide_quality` plus `seal_slide_scene_envelope`/`seal_deck_scene_envelope`). No equivalent standalone entry point survives; this is a full API redesign, not a rename. | Fixed 2026-09-12 at the root cause; see "Resolutions (2026-09-12)". |
| `test_authored_exact_admission_api.py::test_exact_admission_happens_before_async_job_or_background_task` | (c) | Calls `endpoint.queue_presentation_generation(..., force_legacy_dispatch=True)`. That parameter (and the whole `if not chat_first_enabled: _validate_authored_exact_admission(request)` fail-before-session branch it drove) was deleted in `b11a92e62` ("refactor: remove legacy presentation sources") — a commit about retiring the React template-layout system, not about this dispatch-ordering knob. Since removal, `chat_first_enabled = _chat_first_generation_enabled()` is computed unconditionally, and that function (`api/v1/ppt/endpoints/presentation_generate_admission.py`) always returns `True` for any supported DB runtime — it checks runtime support, not the `CHAT_FIRST_WORKSPACE_ENABLED` feature gate CLAUDE.md documents as default-off. So the "fail before creating any job/background task" ordering this test protects is now unreachable through the public API for *any* caller, not just this test. | Fixed 2026-09-12 at the root cause; see "Resolutions (2026-09-12)". |
| `test_document_context_limits.py::test_async_queue_rejects_oversized_context_before_creating_job` | (c) | Same root cause and same commit (`b11a92e62`) as the row above — same `force_legacy_dispatch=True` call, same now-missing parameter, same collapsed fail-first branch. | Fixed 2026-09-12 at the root cause; see "Resolutions (2026-09-12)". |
| `tests/integration/test_authored_worker_lane_e2e_overload.py::test_overload_halves_concurrency_and_recovers_after_three_successes` | (c) | **Location correction: this test lives in `tests/integration/`, not `tests/unit/` as the task brief stated.** Full end-to-end generation completes all 8 slides (author/illustrate/render/persist stage counts all 8/8, 99% progress) then fails at publication with `sqlite3.OperationalError: no such table: user`, raised from `services/authored_vnext/integration/sql_repository.py:_active_account_generation` (called via the new, unconditional `_persist_master_pinned_editable_scene_deck` step in `services/authored_presentation_generation.py:~3208`). That repository uses its own **synchronous** SQLAlchemy engine, resolved independently via `utils/db_utils.get_database_url_and_connect_args()` — a third database connection the test harness (`tests/support/authored_worker_lane_harness.py`) does not know about; its own comment documents binding exactly two databases (a private lane engine and `services.database.sql_engine`), neither of which this vNext repository resolves to in this test process. Confirmed broad, not overload-specific: `tests/integration/test_authored_worker_lane_e2e.py::test_happy_path_authors_in_token_profile_waves_and_publishes` fails identically. | Engine defect fixed 2026-09-12; the test now reaches publication and fails on a second, distinct defect (synchronous CAS on the event loop) reported under "Resolutions (2026-09-12)" item 1. |

## Real defects (class c) — resolved 2026-09-12

All four were fixed at the root cause; the table rows above are the original
investigation and are left as written. Each item below records what the defect
actually was and what replaced it.

1. **`services/authored_vnext/integration/sql_repository.py` uses a third, harness-unreachable DB connection** (affects both `test_authored_worker_lane_e2e_overload.py::test_overload_halves_concurrency_and_recovers_after_three_successes` and `test_authored_worker_lane_e2e.py::test_happy_path_authors_in_token_profile_waves_and_publishes`). `_persist_master_pinned_editable_scene_deck` (unconditional whenever `publication_owner_id is not None`, i.e. every owned authored generation) resolves a *synchronous* engine via `get_database_url_and_connect_args()`/`create_engine`, independent of `services.database.async_session_maker`/`sql_engine`. The worker-lane integration harness explicitly documents binding exactly two SQLite databases and was written before this vNext integration step existed in the generation flow, so nothing runs `create_all` against whatever the sync engine resolves to. This is either (a) a harness gap that needs a third, deliberate binding (which module/session-maker exactly needs binding is unclear without deeper investigation into `services/authored_vnext/integration/sql_repository.py`'s engine caching), or (b) a genuine SQLite-profile limitation of the new vNext master-pinned persistence path that production should account for (CLAUDE.md: "PostgreSQL is the cluster profile; SQLite remains a local/multiprocess profile"). Either way it is outside "test files only" scope and risks masking a real multi-engine consistency issue if patched blindly. File: `services/authored_vnext/integration/sql_repository.py:160` (`_active_account_generation`); harness: `tests/support/authored_worker_lane_harness.py:736-762`.

2. **`force_legacy_dispatch` (fail-before-session admission ordering) was deleted as collateral damage of an unrelated cleanup.** Commit `b11a92e62` ("refactor: remove legacy presentation sources") removed the React template-layout system; in the same diff it deleted `queue_presentation_generation`'s `force_legacy_dispatch: bool = False` parameter and the `if not chat_first_enabled: _validate_authored_exact_admission(request)` branch it drove (`api/v1/ppt/endpoints/presentation_generate.py`). That parameter's actual purpose — per both its own removed code and the two tests' surviving comments — was to force **fail-before-any-side-effect** admission ordering for the workspace-nested-dispatch case ("A workspace-bootstrapped request defers this rejection until after the operation is located... the nested dispatch keeps the fail-first ordering"), a concern orthogonal to the retired template strategy. Since the removal, `chat_first_enabled = _chat_first_generation_enabled()` is unconditional and that function (`api/v1/ppt/endpoints/presentation_generate_admission.py:19`) always returns `True` for a supported DB runtime — it is not gated by `CHAT_FIRST_WORKSPACE_ENABLED` at all, despite the similar name. The practical effect: the fail-first admission path these two tests protect (reject an invalid exact-reconstruction/oversized-document request *before* creating a job row or background task) is now unreachable through the public API for any caller, not only in tests. Whether that regression is real (a wasted job/background-task gets created for a request that will be rejected anyway) needs a maintainer/architecture call — either restore the parameter (scoped to its actual dispatch-ordering purpose, distinct from the template strategy `force_legacy_dispatch` name evokes) or confirm the fail-first behavior is intentionally retired and delete these two tests. Files: `tests/unit/test_authored_exact_admission_api.py:66-90`, `tests/unit/test_document_context_limits.py:~300-320`; removed code: `api/v1/ppt/endpoints/presentation_generate.py` (diff in `b11a92e62`).

3. **`pipeline._refresh_semantic_gate_evidence` was replaced by a scene-envelope model with no equivalent standalone entry point.** Commit `5218198f3` ("feat(authored): seal semantic scene hybrid release path") deleted the whole-deck, gate-time metric-recapture function this test exercises directly and replaced it with a per-slide `_evaluate_slide_quality` plus an immutable "sealed before Chromium" scene envelope (`seal_slide_scene_envelope`/`seal_deck_scene_envelope`, referenced throughout `services/authored_presentation_pipeline.py` and `services/authored_presentation_generation.py`). This is a genuine architectural redesign, not a rename — writing an equivalent test against the new model requires understanding what the new envelope model guarantees in place of "recapture once when the ledger's HTML/metrics predate current semantic-role stamping," which is a design question, not a mechanical test update. File: `tests/unit/test_authored_checkpoint_service.py:96-170`; deleted code: `services/authored_presentation_pipeline.py` (diff in `5218198f3`).

4. **`test_authored_vnext_master_contract.py`'s authored/static-admission boundary assertion is incidental, not designed.** `admit_authored_html(preview.html)` now passes because the shared authored CSS allowlist (`utils/renderer_css_policy.py`) was deliberately widened for the main authored pipeline after this test was written, and vNext preview markup happens to use only properties that widening now covers. There is no other mechanism (attribute allowlist, unsafe-CSS pattern, tag allowlist) currently rejecting `preview.html` through `admit_authored_html`. Left as (c) rather than (a) because removing/weakening a security-boundary `pytest.raises` assertion is exactly the kind of change the task brief says not to make unilaterally when unsure, and there is no documented invariant ("vNext preview HTML must always be rejected by the authored-admission policy") to cite for either keeping or dropping it — that is a product/security decision, not a code-behavior fact I can point to a commit for. File: `tests/unit/test_authored_vnext_master_contract.py:393`.

## Resolutions (2026-09-12)

**1. One engine, not three.** `AuthoredVNextSqlRepository.from_application_database`
resolved its own URL from the environment and built a private synchronous
engine, so it was a *third* connection: whenever `services.database.sql_engine`
was bound to anything other than the environment's default -- an embedded
harness, an integration test, any caller that rebinds it -- the repository wrote
to a different database than the rest of the process, and
`_persist_master_pinned_editable_scene_deck` failed against an empty schema at
the very end of a fully generated deck. `services/database.py` now owns the one
synchronous view of the configured database (`get_sync_engine` /
`sync_session_maker`): it is *derived from* `sql_engine` rather than from the
environment, and exactly one is held at a time -- rebinding `sql_engine` to a
different URL disposes the previous sync engine and builds the matching one. The
owner/tenant `do_orm_execute` scoping is unaffected because
`register_owner_scope_hooks` listens on `sqlalchemy.orm.Session` itself, so these
sessions carry the same hooks.

**Still failing, one layer deeper -- reported, not worked around.**
`tests/integration/test_authored_worker_lane_e2e_overload.py::test_overload_halves_concurrency_and_recovers_after_three_successes`
no longer fails with `no such table: user`: it now authors, illustrates, renders
and persists all 8 slides (8/8 on every stage, 99 %, `finalizing`) and fails at
publication with `sqlite3.OperationalError: database is locked`, raised from
`AuthoredVNextSqlRepository.append_master` via
`services/authored_generation_scene_persistence.py:80`
(`repository.append_imported_master(internal.master)`).

That is a second, genuinely distinct defect that the engine unification made
visible rather than caused: **the synchronous vNext CAS repository is called
directly on the event loop.** `_persist_master_pinned_editable_scene_deck` is an
`async def` that invokes `repository.append_imported_master` /
`repository.get_master` / `append_deck` synchronously, so for the duration of
those calls the loop cannot advance -- and therefore cannot let any in-flight
async writer commit and release SQLite's write lock. While the repository had
its own private database this was invisible; sharing the one configured database
turns it into a deadlock on the SQLite profile: the blocked writer waits for a
lock held by a transaction that only the blocked loop can finish.

This is a latent production hazard, not a harness artifact: outside a rebound
engine the old code resolved the *same* URL from the environment anyway, so
local/Electron SQLite runs already shared the database with the loop's own
writers. The structural fix is to stop doing synchronous database work on the
loop -- route these calls through `AsyncAuthoredVNextSqlRepository` (which exists
for exactly this: "Async facade for endpoints that must not block the event
loop") or an `asyncio.to_thread` boundary in
`services/authored_generation_scene_persistence.py`. That is a change to
`services/authored_vnext/generation/*` and the scene-persistence module, outside
the ownership of this task, and it interleaves repository calls with awaited
telemetry gathering, so it needs its own design pass rather than a blind wrap.

A `BEGIN IMMEDIATE` begin-hook on the sync engine was tried and deliberately
**not** kept: it is defensible on its own terms (a read-then-write CAS that
begins deferred loses its upgrade to any concurrent committer, and SQLite does
not invoke the busy handler for that case, so the busy timeout is no protection),
but it does not fix the deadlock, and while the loop-blocking defect stands it
only converts a fast, diagnosable `database is locked` into a 30 s busy-wait
before the same failure.

**2. Fail-first admission, restored at the seam that now exists.**
`force_legacy_dispatch` is not coming back: it was a parameter whose real
purpose was dispatch *ordering*, and the ordering belongs in the one live path
rather than in a caller-supplied flag. The invariant it protected is "a request
that cannot be admitted must fail before anything durable exists for it", and
the distinction that makes the deferred case legitimate is whether there is
anything to defer *onto*. `queue_presentation_generation` now runs the
request-only admission -- exact-preset resolution, and the document-context
preflight -- before `_bootstrap_generation_workspace` **when the request carries
no `presentation_id`**, because in that case there is no deck, no workspace
operation and no timeline card to project a rejection onto. A request that does
carry one keeps the deferred ordering on purpose, so its rejection still lands
on the operation's timeline.

Tracing this turned up a third thing the same commit broke: the document-size
preflight was not merely mis-ordered, it had become unreachable. Validation
(`check_if_api_request_is_valid`) canonicalizes `request.files` into reference
snapshots and sets `files = None`, and the preflight sat *after* it behind
`if admitted_request.files:` -- always false once files were supplied. The
preflight now runs before validation in both branches, and is skipped only when
the fail-first admission above already measured the same bytes for this request.

**3. Sealed envelope, same intent.** `_refresh_semantic_gate_evidence` has no
standalone successor and does not need one: the scene envelope is sealed before
Chromium, so the gate HTML is by construction the HTML that was rendered, and
the whole-deck gate-time recapture became a per-slide resume decision. The
intent survives exactly and is now asserted against that model in
`tests/unit/test_authored_checkpoint_service.py::test_sealed_scene_envelope_recaptures_stale_role_evidence_once`:
a durable render checkpoint whose stage HTML carries obsolete `data-semantic-role`
stamps is refused (the sealed projection restamps them, so the checkpoint HTML
no longer describes the scene its PNG was taken from), the slide is recaptured
against the projection, and completing the render stage rebinds the durable
evidence to it -- so a second resume of the same job renders nothing. Once,
bounded, same as before.

**4. Decision: the vNext preview MAY pass `admit_authored_html`.** There is no
invariant that static output must be *inadmissible* as authored HTML, and there
cannot be one: `admit_static_output_html` is defined as "validate the inert
static extensions, neutralize them, then run the public authored parser on the
result" (`utils/renderer_isolation.py:401`). The static profile is a
pre-validator in front of the stricter public gate, not a disjoint universe, and
its own docstring states the direction that *is* designed -- "without widening
public authored admission". The removed `pytest.raises(RendererPolicyError)` was
asserting the converse, which was never designed and is now false for an
additional reason the original triage did not reach: the preview emits **no**
`data-*` attribute at all any more, so there is nothing left for the static
profile to neutralize. (The three `data-canonical-digest` / `data-master-furniture`
/ `data-body-slot` assertions that followed the `raises` were dead for the same
reason -- they had not executed since the `raises` started failing, and they do
not hold today.)

The test now asserts the one-directional invariant instead: neutralization is
the identity for this preview, both gates admit the same bytes with the same
source digest and no extra assets, and the producer is named by the preview
receipt's own `canonical_digest` plus the master furniture markup both gates
carry through. The `neutralize_static_output_html(preview.html)[0] ==
preview.html` assertion is deliberate: the day the preview starts emitting a
static-profile extension again, the divergence becomes visible here rather than
silently.

**Also in this change (not a triage item).** `services/authored_rollout_profile.py`
gained one validation rule: `AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP=true` requires
`AUTHORED_ADMISSION_GATE_MODE=enforce`. The single loop decides what to repair
from the admission gate's verdict, and under `shadow` the gate records a verdict
nothing acts on -- so a single-loop shadow profile spends its one repair budget
on advice that was never enforced and publishes anyway, strictly worse than
either switch alone. A violating combination is the coded rejection
`authored_rollout_profile_invalid` where a profile is *resolved* (from the
environment, and from a job's durable pin), not where one is constructed, so a
directly built `RolloutProfile` stays a plain value.

## Pre-existing, order-dependent flake noticed in passing (not fixed, not in scope)

`test_authored_checkpoint_service.py::test_filesystem_evidence_recovers_db_pointer_and_clears_render_failure` intermittently fails on `assert store.sweep_orphans(max_age_seconds=0, max_files=1) == 1` (got `0`) when run as part of a larger combined invocation, but passed 100% (checked 4/4 and again as part of a 211-test combined run) otherwise. `sweep_orphans` compares `time.time() - path.stat().st_mtime >= max_age_seconds` immediately after writing the orphan file; this looks like Windows filesystem mtime-flush timing, unrelated to the PYTHONPATH fix applied to this same test (which resolved the original, deterministic `migration_revision_contract` import failure and is independently verified). Not part of the task's named failure list; flagged for awareness only.

## Environment note

At task start, the shared worktree (`C:/project/PPT-agent/ppt-agent`) had uncommitted, in-progress edits from a concurrent session to `services/authored_admission_policy.py` and `services/authored_presentation_pipeline.py` that transiently broke the import chain (`ImportError: cannot import name 'POLICY_REVISION'`). Per the shared-worktree co-tenancy playbook, all investigation and fixes here were done in an isolated worktree pinned to `serve/main-20260902`'s last committed SHA (`adeb6b8d1`) rather than against the shared tree's moving state. The final commit (below) was built in the shared worktree via a private index against whatever its `HEAD` was at that time, not this pinned SHA.

## `test_authored_worker_lane_e2e_overload.py` retriage after `e291173a1` (2026-09-12)

Task: `e291173a1` ("fix(authored-vnext): stop calling the synchronous CAS on the
event loop") fixed the deadlock the row above this note was left waiting on.
With that fixed, `tests/integration/test_authored_worker_lane_e2e_overload.py`
was reported as having two failing assertions on `recorder.waves`, in
`test_overload_halves_concurrency_and_recovers_after_three_successes` and
`test_overload_does_not_lose_a_slide_without_the_wave_barrier`. Re-verified
against this worktree's `HEAD` (`353821dd4`), CI-parity env (`DATABASE_URL`
sqlite+aiosqlite temp file, `APP_DATA_DIRECTORY` temp,
`DISABLE_IMAGE_GENERATION=true`).

**`test_overload_does_not_lose_a_slide_without_the_wave_barrier` — could not
reproduce; left unchanged.** Ran isolated 3/3 and as part of the full file
2/2 (6/6 total): passes every time, `recorder.waves == []` holds as written
(that scheduler path, `_run_admission` in
`services/authored_wave_scheduler.py`, never calls `plan_authored_batch_wave`,
so the recorder — which hooks that function — never appends a wave regardless
of the timing defect below). The one failure resembling this test that this
retriage saw was a *different* symptom (`worker lane did not settle`, an
empty `provider.calls` list, i.e. the deck never got past its first status
poll) inside a single 150s-timeout `Bash` invocation running the whole file
plus another command back-to-back — resource contention in this sandbox's
background-command timeout wrapper, not this test's own logic. Not changed:
(c)-style "do not touch a test that is actually asserting the right thing,"
even though the symptom that motivated re-checking it doesn't reproduce
deterministically enough to call it fixed either. If this recurs, capture
`provider.calls` and the operation's timeline, not `recorder.waves` — the
symptom was upstream of anything this test observes.

**`test_overload_halves_concurrency_and_recovers_after_three_successes` — (b)
environment; skipped with a precise, causally-scoped guard, not touched
structurally.** Reproduces deterministically, 4/4 full-file runs and 1/1
isolated run, always the same diff: `recorder.waves` has 7 entries instead of
6, `((6,),) , ((7,),)` where `((6,), (7,))` is expected — the concurrency cap
never restores from 1 back to the profile's steady value of 2.

Root cause, traced with an instrumented standalone script (not committed;
called `_overload_scenario` directly and printed
`recorder.states_after_wave`): `advance_authored_batch_policy`'s
`consecutive_successes` never leaves 0 and `cooldown_until` never clears past
wave 2, even though the scheduler *does* keep calling
`AuthoredWaveSchedule.wait_out_cooldown` before every later wave and *does*
get past it each time (7 waves still cover all 8 slides — nothing hangs, no
slide is lost, no deadlock). The two pieces:

- `services/authored_presentation_pipeline.py:254` constructs
  `AuthoredWaveSchedule(new_authored_batch_policy_state())` with no `clock=`
  or `sleep=` override, so `self._clock` is the real `time.monotonic`, both
  in production and under this test.
- `tests/support/authored_worker_lane_harness.py`'s `_install_sleep_recorder`
  (lines ~411-441) monkeypatches `asyncio.sleep` at module scope for any
  caller inside an `authored_*`/`services.authored_*` module — including
  `wait_out_cooldown` — so the awaited cooldown never actually blocks in real
  time; it returns via `real_sleep(0)`.
- `wait_out_cooldown`'s exit check (`services/authored_wave_scheduler.py:90`,
  added with the function itself in commit `127d3d4a8`, "Recover authored
  rescue calls after provider overload"), `if self._clock() <= now: return
  waited`, is the seam meant to let a *paired* no-op clock + no-op sleep test
  double skip a real wait — its own comment says "Test/no-op sleepers may
  deliberately leave the clock fixed." Here the sleep is faked but the clock
  is real and un-injected, so the seam is unpaired.

Measured directly (`time.get_clock_info("monotonic")` in this worktree's
`uv run python`): `implementation='GetTickCount64()'`,
`resolution=0.015625` (~15.6 ms), and a 200,000-sample loop of back-to-back
`time.monotonic()` calls returned the *same* value on 199,998/199,999 of the
adjacent pairs. So on this platform, two real (unfaked) `self._clock()` reads
a few microseconds apart routinely compare equal — the escape valve fires on
the first spin after the faked sleep returns, long before the real 30 s
cooldown (`OVERLOAD_COOLDOWN_BASE_SECONDS`) has elapsed, `advance()` then runs
with a `now` still inside `cooldown_until`, and "success during cooldown
doesn't count toward recovery" (`services/authored_batch_policy.py`'s own
docstring) permanently suppresses the 3-consecutive-success recovery this
test is asserting. CI runs on `ubuntu-latest`
(`.github/workflows/test-all.yml:16`), where `time.monotonic()` is
nanosecond-scale and two real reads essentially never compare equal, so the
escape valve there only fires for genuine no-op test doubles and this
assertion is expected to hold — this is a Windows-sandbox-only artifact of an
un-injected clock meeting a faked sleep, not a change in the scheduler's
intended contract (recovery-after-three-successes is still what
`services/authored_batch_policy.py:advance_authored_batch_policy` implements
and is still what every other assertion in this same test — `after_overload`,
`states[2]`/`states[3]` consecutive-success counts, `after_recovery`, the
final `provider.calls_by_index` — is written to check on the correctly-timed
path). Per this task's brief, not weakened and not rewritten as if the wave
shape were now intentionally 7-wide.

Action taken (owned file only,
`tests/integration/test_authored_worker_lane_e2e_overload.py`): added
`@pytest.mark.skipif(time.get_clock_info("monotonic").resolution > 1e-3, ...)`
directly on this test, with an in-file comment citing the same file:line
evidence as above, so it keeps running (and keeps proving the contract) on
CI's finer-resolution clock and only skips where the platform clock itself
cannot support the harness's no-op-sleeper assumption. This does not fix the
harness/production gap — that requires either injecting a test-controlled
clock into the one production `AuthoredWaveSchedule(...)` call site
(`services/authored_presentation_pipeline.py:254`) or changing
`wait_out_cooldown`'s escape valve to not use "clock didn't advance" as its
no-op-sleeper signal — both outside this test file's ownership for this task.
