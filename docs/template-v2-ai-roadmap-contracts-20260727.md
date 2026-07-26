# Template V2 AI roadmap: bounded contracts

This follow-up implements six independent, fail-closed domain slices. It does
not add an external AI provider, remote image fetch, R2 upload, presentation
clone, or whole-slide JSON replacement. All apply operations remain bounded to
one selected run, chart, table, image, or slide layout.

## Selection rewrite

`servers/nextjs/lib/template-v2-ai-rewrite.ts` validates UTF-16 offsets against
`Intl.Segmenter` grapheme boundaries, splits one selected run, and merges only
adjacent runs whose complete non-text metadata is identical. The workflow
supports shorten, expand, report tone, proposal tone, and translation actions;
returns two or three candidates and deterministic before/after diffs; performs
a text-fit preflight; and separates preview, apply, and cancel. Revision CAS,
idempotency, and stale-source guards fail closed. Only an explicitly injected
deterministic fake provider is accepted.

The `apply-text-selection-patch` Studio action commits the bounded result
through the existing command history. Undo, redo, dirty state, journal
snapshots, autosave, and server `expected_revision` therefore remain the single
authoritative persistence path.

## Chart copilot

`servers/nextjs/lib/template-v2-chart-copilot.ts` validates a strict operation
union before producing a chart-scoped preview. It supports compatible type,
axis, title, grid, legend, and data-label controls plus series add, remove,
reorder, rename, value edit, and bounded data replacement. Quoted CSV/TSV and
table-shaped imports are limited to 64 KiB, 24 categories, 12 series, and
bounded cells. Existing stable series IDs and opaque metadata are retained
where a series matches.

Recommendations are deterministic and based only on the validated data shape.
An optional provider boundary accepts one to three validated candidates and is
default-deny. Radar and other incompatible control combinations fail closed.

## Table transforms

`servers/nextjs/lib/template-v2-table-operations.ts` implements row and column
insert, delete, reorder, header conversion, transpose, bounded CSV/TSV paste,
table-to-chart preview, and long-table split suggestions. Existing cells move
as complete objects. New cells clone a nearby prototype and replace only text,
preserving styles and opaque metadata. Preview digests reject stale sources and
tampering. Detailed limits and apply rules are in
`docs/template-v2-table-operations-contract-20260727.md`.

## Quality inspection

`servers/fastapi/templates/v2/quality.py` produces deterministic reason-coded
findings for text overflow, text below 9 pt, low contrast, slide density, chart
legend/unit issues, excessive table columns, and unsupported or raster-only
elements. Inspection never mutates the source. Fix generation is a separate
preview, and apply validates strict schema, source digest, selected finding,
and revision CAS.

## Local image replacement

`servers/fastapi/templates/v2/local_assets.py` accepts caller-supplied bytes
only. It verifies MIME magic bytes and decoded format, caps bytes, dimensions,
and pixels, and supports PNG, JPEG, and WebP. Replacement changes only the
strict image `data` field. Provenance is returned as a sidecar asset record;
the previous reference is emitted as a deferred retention intent, never an
eager orphan deletion.

Three deterministic crop candidates use only bounded focus and scale fields.
They have their own preview digest, icon compatibility guard, revision CAS, and
explicit selection apply. No arbitrary URL, network client, SSRF surface, R2
operation, or storage credential is present.

## Slide variants

`servers/fastapi/templates/v2/slide_variants.py` previews exactly two or three
data-focused, image-focused, or executive-summary candidates. A candidate may
patch only allowlisted visual fields inside one layout. Strict validation and
semantic digests reject content or server-owned metadata changes. Apply emits a
slide-scoped journal snapshot; cancel is mutation-free; restore accepts only
the matching applied digest and revision.

## Integration and remaining UI work

The new Node tests are part of `npm run test:ci-node`. The modules expose
deterministic, directly testable preview/apply contracts without adding
credentials or network calls. Selection rewrite is also connected to the
existing Studio reducer. Dedicated end-user panels, provider approval/egress,
managed asset storage, and deployment remain separate future slices; none is
claimed here.

## Validation evidence

The stacked follow-up was validated from the PR #89 head with the external
provider, remote fetch, R2, signing, and deployment paths still disabled:

- FastAPI: 1,040 passed and 6 skipped; the 11 new roadmap tests also pass in
  isolation.
- Next.js: the Template V2 Node group passes 161 tests, including the 30 new
  roadmap tests; typecheck, lint, production build, and 29/29 page generation
  pass.
- Browser: the full Chrome component suite passes 105/105 across 24 specs.
- Export: all 12 fidelity tests pass, including four real
  LibreOffice/Poppler comparisons through presentation-export v0.4.2.
- Packaging: 48 packaging/release policy tests pass with one macOS-only skip;
  production dependency findings are zero; Windows NSIS and AppX artifacts
  build with publishing disabled and without a signing certificate; packaged
  FastAPI, Next.js, and runtime Tailwind smoke checks pass.

The first Next.js build attempt was interrupted only by a transient Google
Fonts network connection failure. An unchanged retry completed successfully.
Managed PostgreSQL, production credentials/egress, R2, release publication,
code signing, notarization, and deployment were not run and are not claimed.
