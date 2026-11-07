# SVG template pictures implementation result — 2026-11-08

## Outcome and provenance

Implementation and independent read-only review are complete in the isolated
`svg-template-pictures-1107` worktree, based on
`8d6d21fb8611fe6b6312fc2d4ba5186406aa05d6`. This is a native collaboration
recovery result. It does not certify the earlier Orca task or its false-ready
state. The local commit identifier is supplied in the coordinator handoff.

## Owned changes

- `servers/fastapi/services/generation_core/drivers/svg/asset_slots.py`
- `servers/fastapi/services/svg_templates/picture_policy.py` (approved additional ownership)
- `servers/fastapi/services/generation_core/assets/offer.py`
- `servers/fastapi/services/generation_core/assets/vector_offer.py`
- `servers/fastapi/services/generation_core/assets/infographic_plan.py`
- `servers/fastapi/utils/llm_calls/draw_vector_illustrations.py` (approved optional argument plumbing)
- `servers/fastapi/tests/unit/test_svg_template_picture_assets.py`
- `servers/fastapi/tests/unit/test_svg_template_picture_policy.py`
- This report.

The enabled route reuses the existing vector asset offer. It does not construct
an image provider or read the live illustration-mode/parts settings. It plans
content-relevant scenes, step pictures, comparison pairs and pictographs, and
excludes cover/section/closing pages from all picture parts. Art direction uses
only the frozen template projection's color and font tokens, without extracted
template wording, reference examples or a different authored style's brief.
Optional planning/drawing failures and explicit picture prohibitions remain safe.

The physical drawing-call budget, including structured-parser and drawing
retries, is the smaller of the existing vector `MAX_CALLS=12` and the existing
runtime profile's illustration budget. There is no new global scene-frequency
cap. Existing infographic planning and icon-family selection remain separate
existing stages; twelve is not a claim about total calls across the whole offer.
Zero drawing budget still permits call-free pictographs. Existing checkpoint
offers replay before policy evaluation and are never rebuilt or relabeled.

## Root integration API and ownership

The dependency-light module is `services.svg_templates.picture_policy`:

- `TEMPLATE_PICTURES_REVISION` is `svg-template-pictures/v1`.
- `template_picture_policy(request, snapshot)` returns the effective policy only
  for a pinned SVG template-redesign request with literal boolean
  `snapshot.template_pictures is True`, the exact revision in
  `snapshot.template_pictures_revision`, and a template-redesign selection.
- The revision defines parts `scene`, `spots`, `pairs`, `pictograph`, maximum
  twelve drawing calls, and cover exclusion. No additional snapshot fields are required.
- `template_picture_guidance(snapshot, body_bbox=None)` returns an empty string
  for missing/off/unknown-revision policies. When enabled, it refers to the
  authoritative `template_page.body_region`, preserves aspect ratio/readable
  facts, and permits optional omission. An optional x/y/width/height box adds
  page-local coordinates without creating fixed slots.

Root approved fresh-only pinning in `apply_template_snapshot` and enabled-only
guidance insertion in `body_author_rules`. Those root-owned edits are not part
of this worktree. Root also retains environment registry/accessors, admission,
verifier ledgers, SVG subset registration, runtime loading and artifact review.
Register the two new SVG test files in the root-owned subset if required.
No shared authored model or infographic LLM prompt helper was edited.

## Independent review

Separate Sol/high developer and Sol/high read-only reviewer roles were used.
The reviewer reported no unresolved critical, high or medium findings. Its
coverage recommendation was incorporated as a real
`resolve_offer -> _build -> build_asset_offer` integration test with only brand
resolution and the planner mocked. The test verifies frozen style/brand/policy
propagation, absence of image-service construction and optional planning failure.
The reviewer ran no tests and made no edits.

## Actual validation

One narrow pytest command was selected after implementation and review:

```powershell
& 'C:/project/PPT-agent/ppt-agent/servers/fastapi/.venv/Scripts/python.exe' -m pytest tests/unit/test_svg_template_picture_assets.py tests/unit/test_svg_template_picture_policy.py tests/unit/test_svg_template_step_pictures.py::test_resolve_offer_turns_on_vector_spots_for_a_template_deck_while_the_mode_is_off tests/unit/test_svg_template_step_pictures.py::test_a_stored_offer_is_replayed_unchanged_after_the_knob_changed tests/unit/test_vector_illustrations.py::test_the_default_parts_draw_only_cover_and_scene_pictures tests/unit/test_vector_illustrations.py::test_asset_projection_legacy_drawing_normalization_matches_7c_bytes tests/unit/test_generation_core_asset_offer.py::test_offer_plans_one_illustration_per_scene_and_library_icons tests/unit/test_generation_core_asset_offer.py::test_image_enabled_offer_is_unchanged_by_the_icons_only_mode -q
```

Working directory: this worktree's `servers/fastapi`.

- Initial execution: **9 failed, 38 passed in 17.18 seconds**. Eight failures
  lacked pictographs; one unchanged legacy case lacked icons.
- Diagnosis: this worktree used `core.autocrlf=true`; the vendored Lucide nodes
  were checked out as CRLF while the manifest authenticates the LF Git blob's
  raw bytes. The loader correctly rejected the changed byte digest.
- Root explicitly approved restoring only
  `servers/fastapi/assets/icon_families/lucide/nodes.json` from the exact current
  HEAD blob after checking no source diff and equality after CRLF normalization.
  A binary subprocess read and binary write performed that restoration. No
  manifest, loader, index content, test assertion or other asset was changed.
- Restored canonical SHA-256:
  `fed11f99639ce07c027178d7c925c7ebf19e1468f4eb5f58792c258a7c022d31`.
  Prior CRLF SHA-256:
  `d180d6b9d873f28e0cfb87532eebde8ce157f1b780c6abe787bac819248a46d4`.
- The independent reviewer accepted the dependency-byte correction and identical
  scope rerun. After that actual loading dependency changed, the same command
  produced **47 passed in 8.37 seconds**, exit code zero.

No broader verification command was run. Tests use synthetic data, mocked
providers and the existing isolated test fixtures; no live calls were made.

## Frozen-scope evidence and limits

The starting worktree was clean. Source changes are confined to the owned paths
above. Existing non-SVG entrypoints, dedicated tests, CI, shared authored models,
admission, environment accessors and revision ledgers were not changed. The
selected existing default offer/image-enabled compatibility, legacy drawing
byte-normalization and template-step routing/replay cases passed unchanged.
Absent policy arguments are omitted from legacy downstream calls, so old
planner/drawing kwargs, prompts and offer receipts retain their existing path.

Managed-stack loading, PostgreSQL/RLS, authenticated routing, live provider/judge
measurements, actual exported PPTX/PDF output and visual acceptance were not
performed by this session; they remain root-owned integration work. No source
checkout outside this worktree, app data, live database, deployment or remote
Git state was modified. The compiled-spec-memory skill was not available at the
expected project/user locations; no skill-based recording is claimed.
