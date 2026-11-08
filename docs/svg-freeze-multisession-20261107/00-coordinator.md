# SVG freeze: independent multi-session execution (2026-11-07)

This refines the owner's latest instruction: split separable work into independent Orca sessions, and use appropriately sized internal multi-agent teams in each. Root remains the orchestrator and owns integration and managed-stack/runtime/PPTX validation. Non-SVG engines and dedicated tests stay frozen in behavior, contents and execution participation. The controlling scope is ../codex-brief-svg-freeze-quality-20261107.md.

## Session plan

| Session | Lead | Primary write scope | Dependencies |
|---|---|---|---|
| 01 chart integrity | Sol high | Native chart requirements/evidence and SVG chart tests | Independent from 02/03/04; revision ledger/admission root-owned |
| 02 evaluation | Sol high | Eval accounting, metrics, comparisons and SVG-eval tests | Root later connects session 04's new metric module |
| 03 template pictures | Sol high | Existing vector asset offer and SVG-specific picture policy | Root supplies snapshot/env and body-placement wiring |
| 04 content sizing | Sol medium | Body prompt, new sizing metric helper and SVG tests | Root supplies snapshot/env and evaluator hooks |

Each spec defines role-specific models: Luna medium for bounded reading, Terra medium for a fixed prompt edit, Sol high for substantive implementation/review, Astra high for chart/compatibility ambiguity. Every session must actually use separate implementation and independent review roles, with at most three active agents including its lead. Do not start extra coordinator hierarchies. Root owns shared files and final checks; no session starts local servers or paid generation.

The four owned scopes are intentionally disjoint. Shared-file requests go to root before editing. Workers must finish their owned implementation, internal review and one smallest relevant check, then return a report/commit and exact integration requests. A missing central hook is explicitly reported, not hidden behind a claim that the integrated feature already works.

## Root integration after results

Reconcile only accepted topic changes while preserving user edits. Add default-off env accessors/registrations; pin picture and sizing policy on fresh template-redesign admission only; connect generation/placement/metrics; verify the new chart revision's legacy compatibility before any current-revision ledger entry. Check frozen files/tests against the starting state without overwriting concurrent changes. Perform only checks justified by integrated changes or gaps in worker evidence.

Then use scripts/start-local-stack.ps1 and existing ports/environment, require PostgreSQL and restart only affected components. Prove actual code loading and runtime behavior, inspect exact exported artifacts, and keep unmeasured quality distinct from code/test success. Newly paid deck/judge runs require concrete counts and any still-required approval. No push, migration, deletion, retirement or deployment is included.

## Launch records

The coordinator records actual Run/Dispatch/workspace handles after successful starts. Pending or unknown launch is not a completed launch; inspect its receipt and never blindly duplicate it. The Orca orchestration skill owns wake-driven coordination; do not use polling scripts or consuming inbox checks from helper terminals.

Active Run: `run_140c2e5d0d3b`. All four task starts returned `ready`; implementation and integrated validation are not yet complete.

| Session workspace | Task | Current Dispatch | Lead model / effort |
|---|---|---|---|
| C:/Users/ibiz/orca/workspaces/ppt-agent/svg-chart-integrity-1107 | task_ab0d31ad312e | ctx_ab24b409616f | gpt-6.1-sol / high |
| C:/Users/ibiz/orca/workspaces/ppt-agent/svg-eval-integrity-1107 | task_f23c0883dd22 | ctx_898df841a22a | gpt-6.1-sol / high |
| C:/Users/ibiz/orca/workspaces/ppt-agent/svg-template-pictures-1107 | task_662be07b515e | ctx_41718af8a08a | gpt-6.1-sol / high |
| C:/Users/ibiz/orca/workspaces/ppt-agent/svg-content-sizing-1107 | task_a95fed3e0853 | ctx_19e3a900b72f | gpt-6.1-sol / medium |

The first chart and picture attempts timed out before task input. Orca identified their terminals as reclaimable; worker-release confirmed closed_agent_terminal, then each original Task was retried once in its existing workspace. Keep this history; do not call them successful first starts.

Initial evaluation integration decision: use an optional sanitized evaluation_identity record and explicit unavailable fields. Do not add a public API response contract or infer backend loading from the harness checkout. Root owns verified runtime identity and the Session04 sizing-metric hook after topic integration.

Owner correction after chart review: the bar-only integrity implementation is NOT accepted as completion. Follow-up Task `task_3ab9beffa2fa`, Dispatch `ctx_ac9c70094f66`, now owns complete coverage of the existing bar/column/line/area/pie/donut contract and valid supported configurations. Message `msg_62795eb31afc` supersedes the earlier bounded-guidance direction. Preserve the earlier test/byte-parity history but do not use it to promote the limited implementation. Root must not silently trade valid-chart support for stricter-looking validation.

Additional dependency (2026-11-08): Session05, `05-chart-render-proof.md`, owns the optional v5 same-capture chart paint evidence extension through the existing Chromium/worker/cache path. Root delegated this narrow shared-renderer scope; chart session retains manifest construction and pre/post-render consumers. Legacy/non-SVG/default carriers remain absent and their contracts remain frozen. The evidence boundary is the exact captured viewport/device scale and final PNG, not arbitrary zoom or final PowerPoint rendering. Session05 must agree the minimal schema/signatures through root before depending on a speculative interface.

Session05 execution recovery: Orca Task `task_842bdb903824` failed before task delivery twice (`ctx_91d15e967adf`, then `ctx_45a38c7abe01`) with agent_readiness timeout/session_not_reported. Both exact owned terminals were released with confirmed closed_agent_terminal receipts. No third CLI launch or new Orca Task was used to hide that failure. Root's native collaboration team `/root/chart_render_team` (gpt-6.1-sol/high, with explicitly assigned Astra/Sol internal roles) now performs the same scoped work in the retained isolated workspace `C:/Users/ibiz/orca/workspaces/ppt-agent/svg-chart-render-proof-1108`. Report that team's outcome separately; the failed launches are not successful tasks. Chart-to-renderer schema coordination still passes through root.
