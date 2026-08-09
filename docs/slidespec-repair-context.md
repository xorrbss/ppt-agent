# SlideSpec repair-context module boundary

`services.slidespec_authored_executor._repair_context` remains the compatibility
facade used by the authored review pipeline. Its deterministic context builder
is split into `services/slidespec_repair_context/` so executor orchestration,
storage, rendering, LLM calls, and acceptance side effects stay outside the
repair projection boundary.

The ordered bands are:

1. `state.py`: normalize browser metrics once and build layout, binding, qid,
   parent, and semantic indexes.
2. `owner_*.py` and `geometry.py`: prove movable imported owners and derive
   Chrome-grounded text, overflow, connector, and collision measurements.
3. `base_projection.py` and `constraint_projection.py`: preserve priority order
   while projecting direct repair buckets and DOM/hard-constraint issues.
4. `issue_groups.py`, `composition_groups.py`, and
   `glyph_compositions.py`: retain issue identity, reference insertion order,
   owner coverage, and bounded compositions.
5. `exact_*.py`: append bottom-stack and vertical-pack exact projections in the
   historical order.
6. `builder.py` and `policy.py`: assemble the unchanged payload shape and expose
   the existing operation, affected-block, and composition limits.

Behavior-preservation invariants:

- The executor facade keeps its four positional-or-keyword parameters and
  forwards the executor's runtime `RenderRequest` type and `render_slidespec`
  callback. This preserves existing monkeypatch and native-render seams.
- Imported, native, and direct `SlideSpec` inputs remain distinct. Native qids
  are rendered only for a render request without an imported source.
- Per-call issue and proof dictionaries are shared across ordered bands; no
  module-level mutable repair state or cache is introduced.
- Result list/tuple shapes, first-seen diagnostic reference order, digest input
  order, and fail-closed Chrome proof checks are unchanged.

Run the narrow behavior checks from `servers/fastapi`:

```powershell
uv run pytest tests/unit/test_slidespec_repair_context_facade.py `
  tests/unit/test_slidespec_pipeline_performance.py `
  tests/unit/test_slidespec_geometry_candidates.py -q
```

Real-browser and publication coverage is provided by
`tests/integration/test_slidespec_page11_browser_canary.py` and
`tests/integration/test_slidespec_authored_executor.py`. The tracked 23-label
fixture is a replay contract, not 23 complete repair inputs; the 90-slide
source-unchanged gate requires the configured private presentation database and
must be run in dry-run mode with `scripts/slidespec_pipeline_benchmark.py`.
