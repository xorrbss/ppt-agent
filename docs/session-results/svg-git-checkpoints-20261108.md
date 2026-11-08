# Git checkpoints — 2026-11-08

The owner requested committing, pushing and merging the completed current work,
and preserving uncommitted work in the other worktrees. Eight source/checkpoint
branches have been pushed to GitHub. The current reviewed root integration is
the delivery authority; worker checkpoints retain intermediate topic history.

| Worktree topic | Branch | Commit | Main integration |
| --- | --- | --- | --- |
| claude-generation-performance-impl-20261022-01 | `archive/generation-performance-20261022-20261108` | `c4c1d36b60b8ccae65867924ad905f812e483042` | Historical preservation only |
| claude-generation-retry-safety-fix-20261022-01 | `archive/generation-retry-safety-20261022-20261108` | `963bb3625d83430376caa807d454ac7ac6f0f974` | Historical preservation only |
| svg-roadmap2-20261026 | `archive/svg-roadmap-tests-20261026-20261108` | `f43081351607fd20e60a90212de9adb2cb8183f1` | Historical preservation only |
| svg-chart-integrity-1107 | `xorrbss/svg-chart-integrity-1107` | `0e0300e9b97595dc8e9334253142253e1e7bdc73` | Reviewed final source is included by root integration |
| svg-chart-render-proof-1108 | `xorrbss/svg-chart-render-proof-1108` | `2ceb6371c66d66dd4694b6f5f89c9371c49587da` | Reviewed final source is included by root integration |
| svg-content-sizing-1107 | `xorrbss/svg-content-sizing-1107` | `d852de2b1508287f8437a4b2a6e09cd7f3eb9d9b` | Reviewed final source is included by root integration |
| svg-eval-integrity-1107 | `xorrbss/svg-eval-integrity-1107` | `f9d65b846c10fbec156cd14b22cca686d759e1ee` | Reviewed final source is included by root integration |
| SVG template pictures | `xorrbss/svg-template-pictures-1107` | `3b7048e355a19ca3554f78261d2e7915d3b0a945` | Reviewed final source is included by root integration |

The three historical backups are intentionally not merged over current main.
All retry-safety snapshot files and all roadmap test files were independently
located in main history. Most performance snapshot files were also located;
seven reflect an earlier intermediate implementation. The current main has
subsequent fixes that these older snapshots must not replace.

No worktrees or local capture artifacts were deleted. Test caches, runtime
capture/publication payloads, credentials and app_data remain local. The unrelated
pre-existing national-ai-msp-proposal style assets were left untouched.

No application source bytes changed during these checkpoint commits. Existing
focused test and runtime outcomes remain historical evidence with their original
source and artifact bindings. No new full-suite result is claimed.

The nine-page chart artifact passed its bounded checks; broader real-document,
pictures, content-sizing, connector and quality evaluations remain open. See
[remaining work](svg-work-remaining-20261108.md) and the
[scoped verification report](svg-freeze-final-verification-20261108.md).
