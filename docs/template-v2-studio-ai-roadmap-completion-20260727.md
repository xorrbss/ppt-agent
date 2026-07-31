# Template V2 Studio AI roadmap completion (2026-07-27)

## Scope

This follow-up completes the remaining bounded Template V2 Studio user flows on
top of the domain contracts in the AI roadmap branch:

- chart copilot controls, bounded CSV/TSV import, deterministic recommendations,
  preview/apply/cancel, and incompatible-control fail-closed behavior;
- table structure editing, bounded CSV/TSV import, table-to-chart preview/apply,
  and long-table split guidance;
- deterministic quality findings with stable reason codes and a separate,
  explicit fix preview/apply flow;
- local-only image replacement with magic-byte, MIME, byte, dimension, and
  pixel validation plus deterministic crop candidates;
- slide-scoped data, image, and executive variants with semantic/render
  digests, preview, explicit apply, cancel, and journal restore.

All mutations are bounded to an existing element or the active slide layouts.
They preserve stable IDs and unknown metadata, join the existing undo/redo and
autosave journal, and fail closed for stale revisions, changed preview digests,
or locked elements. No whole-presentation AI JSON replacement is introduced.

## Safety boundaries

- Recommendations and findings are deterministic and local. No paid LLM,
  vision, or other provider is called.
- Image replacement accepts local PNG, JPEG, or WebP bytes only. Remote URLs,
  SSRF-capable fetches, and R2 uploads remain unavailable.
- Image provenance and deferred orphan cleanup metadata are retained in the
  bounded patch.
- Quality inspection never silently fixes content. Only explicitly previewed
  safe fixes can be applied.
- Variant patches are slide-scoped visual patches. They do not clone the
  presentation or change server-owned metadata.
- `presentation-export` remains pinned at `v0.4.2`; dependency locks are
  unchanged.

## Selective integration basis

The implementation follows the content-level selective integration decisions
recorded in:

- `compatibility/selective-integration-ledger-20260726.json`
- `docs/upstream-selective-integration-report-20260726-followup.md`

No upstream branch was merged or cherry-picked wholesale.

## Validation

Validation was run in increasing scope:

- Next.js targeted and full Node suites: 296 passed, 1 platform-specific skip.
- Next.js TypeScript, ESLint, and production build: passed.
- New component suites plus Studio integration: 26 Cypress tests passed.
- Template V2 export fidelity: 12 tests passed with the pinned export runtime,
  including LibreOffice/Poppler visual comparisons.
- FastAPI full suite: 1,040 passed, 6 skipped.
- Electron packaging preflight: 24 tests passed.
- Windows unsigned NSIS/AppX packaging: completed; unsigned structure
  verification passed.
- Packaged server smoke: FastAPI `/docs`, Next.js `/`, and runtime Tailwind all
  returned HTTP 200.

Signing, notarization, managed PostgreSQL, managed provider credentials,
external object storage, and production deployment were intentionally not
attempted because the required external identities and services are not
available in this environment.
