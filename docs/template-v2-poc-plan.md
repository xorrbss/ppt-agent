# Template V2 PoC plan

## Scope

Template V2 is a presentation/template data-format change, not a REST API
version. The public routes remain under `/api/v1`. This first phase does not
change existing presentations, the default template picker, or the authored
HTML/hybrid export pipeline.

## Current implementation status

The isolated phase-one contract is implemented without production routing or
database changes:

- `services/template_v2_poc.py` defines a strict `v2-standard` title/body
  schema, stable slide/node ID validation, one adaptive-only adapter, an
  export-ready JSON contract, and a fixture repository used to verify
  create/save/reopen/duplicate/Undo/Redo behavior.
- `tests/fixtures/template_v2/internal-title-body.v2.json` is the first new
  internal template fixture. It does not replace or relabel an existing
  template.
- `services/template_v2_rollout.py` implements default-off discovery/creation,
  explicit allowlisting, kill-switch-safe reads and exports for existing marked
  rows, and content-free structured observations.
- Unit and contract tests reject unknown nodes and fields, duplicate IDs,
  authored/HTML output, unstructured observation values, and accidental
  content logging.

The current implementation is deliberately not wired into production
endpoints, persistence, or the UI template registry. The repository has no
production `presentation.version` storage migration for this format, and those
call sites currently contain unrelated user work. Before internal traffic is
enabled, add a reviewed migration and connect discovery/create/read/export to
these boundaries without introducing `/api/v2`.

## Compatibility boundary

The current fork has two distinct rendering contracts:

- Adaptive/template presentations store React-template layout and editable
  structured slide content.
- Authored presentations store `mode: "authored"` (with legacy sentinels), an
  authored HTML source, and a fidelity image. Hybrid export selectively promotes
  supported HTML elements to editable PowerPoint shapes while retaining a
  raster backplate.

Template V2 must sit behind an adapter instead of becoming a third implicit
interpretation of either contract:

```text
Template V2 definition
  -> V2 validation and normalization
  -> PresentationTemplateAdapter
       -> existing adaptive slide/layout contract
       -> existing authored HTML source contract (explicit opt-in only)
  -> existing persistence and /api/v1 endpoints
  -> existing fidelity/hybrid export
```

The adapter output must be one of the existing runtime contracts. It must not
teach authored-hybrid extraction to interpret Template V2 nodes directly.
In phase one, only the adaptive output path is enabled. The authored adapter
boundary is documented so a later experiment can reuse the existing authored
HTML contract, but no V2-to-authored conversion is shipped or selected by the
phase-one flag.

## Feature flags and rollout

Use a server-controlled capability plus an explicit presentation-format marker:

- `ENABLE_TEMPLATE_V2_POC=false` by default.
- `TEMPLATE_V2_POC_TEMPLATE_ALLOWLIST` contains the exact internal template IDs
  permitted for discovery and creation.
- `presentation.version = "v2-standard"` only for newly created PoC
  presentations.
- No inference from a template ID or from `/api/v2`; existing rows without the
  marker stay on their current path.
- The UI shows V2 templates only when the capability is enabled.

Roll out in this order:

1. Add schema validation, adapter contract tests, and round-trip fixtures.
2. Enable one newly created, non-authored user template for an allowlisted
   internal cohort. Do not copy or relabel an existing user template as V2.
3. Verify create, save, reopen, duplicate, Undo/Redo, PPTX, and PDF behavior.
4. Add more newly created user templates, then expand cohorts, only after the
   promotion criteria below remain green.
5. Consider migration only as a separate, reversible project. Do not rewrite
   existing built-in, adaptive, or authored presentations in place.

Promotion requires zero validation-related data-loss events, successful
save/reopen and export rates no worse than the equivalent adaptive flow, and no
increase in hybrid-export regressions during the observation window. Define the
cohort size and observation-window duration in the release checklist rather
than silently widening the flag.

## Observability and rollback

Record the format marker, template identifier, adapter result, validation error
code, save/reopen result, export type/result, and fallback reason. Do not log
authored HTML, slide text, prompts, or other presentation content. Dashboards
must separate V2 PoC traffic from the existing adaptive and authored paths and
show:

- creation and adapter-validation failures;
- save/reopen, duplicate, and Undo/Redo failures;
- PPTX/PDF export failures and raster-fallback counts;
- attempts to read a marked V2 presentation while creation is disabled.

Rollback is a kill-switch operation, not a data rewrite:

1. Disable creation and template discovery with
   `ENABLE_TEMPLATE_V2_POC=false`.
2. Keep the V2 reader/adapter available for already-created marked rows so they
   can still be opened and exported.
3. Preserve the marker and original V2 payload; never downgrade it by writing
   adapted output back as an unmarked legacy presentation.
4. If safe read/export cannot be maintained, make affected rows read-only,
   surface an actionable error, and retain recovery fixtures before shipping a
   repair.
5. Re-enable creation only after the failing fixture becomes a regression test
   and the original cohort passes the full gate again.

Any validation-related data loss, inability to reopen a saved PoC presentation,
or regression in existing authored/adaptive exports is an immediate rollout
stop.

## Test plan

- **Schema unit tests:** accepted V2 fixtures, unknown node rejection, required
  fields, stable IDs, deterministic normalization, and unsupported-version
  errors.
- **Adapter contract tests:** golden adaptive outputs, no mutation of the V2
  source, idempotent adaptation, and proof that phase one cannot select the
  authored adapter path.
- **Persistence integration tests:** create/save/reopen/duplicate plus
  Undo/Redo across reload, including flag-off reads of previously created V2
  rows.
- **Export integration tests:** editable adaptive PPTX and PDF output from the
  PoC template, with existing authored fidelity and authored-hybrid suites run
  unchanged as regression gates.
- **Routing tests:** API discovery/OpenAPI and frontend proxy assertions contain
  `/api/v1` paths and introduce no `/api/v2` route.
- **Rollback tests:** disabling the flag prevents new V2 creation and template
  discovery without changing or orphaning marked rows; re-enabling it restores
  creation without migration.

Each rollout template gets a versioned round-trip fixture and export golden.
Failures must be reproducible from fixture data without production presentation
content.

## Required PoC gates

- Unknown V2 node types fail validation before persistence.
- Adapter output passes the existing slide/content schema.
- Saving and reopening preserves stable IDs and editable content.
- Disabling the flag hides V2 creation but still permits safe read/export of
  already-created PoC rows.
- Authored fidelity export remains byte-for-byte on its existing route.
- Authored hybrid tests retain the 9 pt minimum text size, axis-aligned line
  correction, text-fit safeguards, and raster fallback for unsupported
  elements.
- Public OpenAPI paths remain `/api/v1`.

## Out of scope for phase one

- A new REST `/api/v2`.
- Bulk conversion of existing templates or presentations.
- Replacing authored HTML with Template V2 nodes.
- Direct Template V2 handling inside the hybrid PPTX assembler.
- Making the PoC format the default for existing or new general presentations.
