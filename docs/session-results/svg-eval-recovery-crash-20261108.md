# SVG evaluation recovery crash-window correction

Outcome: fixed and independently reviewed; the single narrow accounting check passed.
This is a follow-up to the accepted evaluation change in the retained worktree, not a replacement of root integration.

## Current-dispatch changes and integration boundary

- `servers/fastapi/scripts/svg_template_eval/run_eval.py`, `recover()`, retained-worktree lines 600–601:
  set the recovered completed record's `presentation_id` and `measurement_status` to pending before the first `save()`.
  Remove the subsequent redundant presentation-ID assignment after the non-completed guard. Reconcile only this
  small hunk, between the outcome/seconds update and first save, into root's accepted file.
- `servers/fastapi/tests/unit/test_svg_template_eval_accounting.py`, line 179:
  add `test_recover_resumes_measurement_after_initial_pending_persistence_interruption` to the existing test file.
- This report.

Do not replace root's whole `run_eval.py`: its separately accepted `measure_deck` content-sizing hook must remain.
No `measure_deck`, sizing module, other evaluator module, reserved shared file or controlling-root checkout was edited
in this dispatch. Earlier accepted changes remain present in this worktree and are not new handoff hunks.
No commit was created.

## Evidence

Previously, interruption after first persistence could leave a newly discovered completed record without pending
measurement status; the next recovery then skipped it permanently. The new test performs the real atomic JSON
write, reads the persisted pending record, and raises a `BaseException` sentinel before any measurement. It then
restarts recovery twice and confirms exactly one measurement, stable attempt/run identity, no duplicate completed
attempt, and unchanged failed/unknown-attempt history. Existing failed/unknown outcome branches and measurement-error
handling remain unchanged.

Implementation developer and independent read-only reviewer: separate `gpt-6.1-sol` agents, high reasoning.
Reviewer found no remaining material issue. Neither agent executed checks.

Exact single lead check, from this worktree's `servers/fastapi`:

`C:/project/PPT-agent/ppt-agent/servers/fastapi/.venv/Scripts/python.exe -m pytest tests/unit/test_svg_template_eval_accounting.py -q`

Result: exit 0, **13 passed in 3.29s**. No other check was executed.
All DB, generation, export and judge boundaries in the crash regression are replaced by bounded synthetic fixtures;
the JSON persistence itself is real. No paid, server, DB, app-data, auth, deployment or external publishing action occurred.

Remaining work: root reconciles only the identified recovery hunk and named test into its accepted evaluator,
preserving the root-owned content-sizing hook. No additional live/runtime or artifact result is claimed.
