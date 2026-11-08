# Source preset compatibility repair — 2026-11-08

## Scope and provenance

The pre-existing source-preset feature changes were preserved. This repair only
excludes `straightConnector1` from the newly added registry fallback when the
existing connector branch has not matched. A previously accepted shape-role
`straightConnector1` therefore retains its rectangle output; connector-role
inputs retain the existing line branch. Other newly recognized presets retain
the registry conversion path. No ledger, anchor, contract revision, registry,
server, database, or external state was changed for this repair.

The selected baseline is the actual local Git HEAD inspected before the repair:
`8d6d21fb8611fe6b6312fc2d4ba5186406aa05d6`. Its source-layout module is read with
`git show` and loaded under a temporary module alias in memory. The checkout is
not replaced. Baseline layout LF SHA-256:
`8a6233fdae6a41211fc0c379ab6bfa47100799ee1e455cad9b1ff0ff09c97426`.

The differential probe intentionally holds imported dependencies at the current
checkout state. It isolates the source-layout module delta and does not prove
whole-checkout or export compatibility.

## Source fingerprints

All hashes below are SHA-256 after CRLF-to-LF normalization.

| File | Before repair | After repair |
| --- | --- | --- |
| `servers/fastapi/services/svg_source_layout.py` | `6fd485abeb43f63e704a453aad1315f1f6a5b2550844e8669301404b442aa813` | `21af8290309c7057affdb523730c7c22253207e3374aa43f1db4f9e1a67a8c29` |
| `servers/fastapi/tests/unit/test_svg_source_layout.py` | `0a534e0b20bb0ae1b4d7d28bf006e99455088a294ac6506bedfe0fd89597c644` | `077e1c2cfff4bfb6f302fee861c4b66c04ef6f3309621212205c1abe2ca814b3` |
| `servers/fastapi/services/svg_source_preset_shapes.py` | `13da0920f0a031c75f42c2be3cce85c0544048ccd612ace5e9de3ab7040cac51` | unchanged |
| `servers/fastapi/services/svg_export_whole_page_verifier.py` | `0e40404f21d6c8588fdbe6b39733710f44f2ffb01dc5657043b58b8bd0c5793a` | unchanged |

These are inspected local source fingerprints, not identities recovered from
the earlier 35-page persisted replay. That historical replay and its 84-test
result were not matched to current source bytes and are not acceptance evidence
for this repair.

## Selected check

Append-only tests cover the shape-role rectangle regression plus an opt-in
baseline/current differential for the seven legacy presets, connector-role
`line` and `straightConnector1`, and numeric custom geometry with cubic and
quadratic curves. Each differential case uses the same fixed manifest and plan,
compares UTF-8 SVG bytes and serialized source-receipt bytes, and verifies the
baseline receipt with both implementations. The same selected pytest invocation
also covers existing preset determinism, unknown-preset rejection, and connector
flip behavior.

Independent read-only review approved the guard and appended tests before the
selected check, confirmed the before-state hashes, and found no scope or
user-change preservation issues. Its acceptance is limited to basic fill/stroke
fixtures and the current-dependency, module-only comparison.

One selected invocation ran with
`SVG_SOURCE_LAYOUT_BASELINE_REVISION=8d6d21fb8611fe6b6312fc2d4ba5186406aa05d6`:

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_svg_source_layout.py -q -k 'test_straight_connector_preset_preserves_legacy_shape_role_rectangle or test_legacy_source_layout_matches_selected_baseline or test_presets_preserve_shape_occurrence_as_editable_svg or test_unsupported_content_fails_preflight or test_straight_connector_flip_preserves_endpoint_direction'
```

Result: exit **1**, **35 passed, 1 failed, 59 deselected** in **6.37 seconds**
(shell elapsed **7.864 seconds**). The raw log is retained at
`docs/session-results/svg-source-preset-compatibility-20261108.pytest.log`.

All ten baseline/current differential cases passed, including the shape-role
`straightConnector1` counterexample. The existing selected preset, unsupported
content, and connector-flip cases passed. The added standalone regression failed
because its unqualified first-rectangle selector matched the background rectangle
instead of the source shape. The selector was subsequently narrowed to the
`source-element-1` rectangle. No production code changed after the invocation.
After explicit authorization for a result-changing test correction, the same
selected command ran once more. It returned exit **1**, **35 passed, 1 failed,
59 deselected** in **6.20 seconds** (shell elapsed **7.631 seconds**). The second
raw log is retained at
`docs/session-results/svg-source-preset-compatibility-20261108.pytest-rerun.log`.
All ten differential cases and all selected pre-existing cases again passed.
The standalone regression still failed because the assumed element ID was wrong:
`svg_source_layout.py:459` paints shapes before text, so this shape is assigned
`source-element-0`, not `source-element-1`. A binding-derived element lookup was
needed to assert the rectangle without assuming paint order. Both failures are
retained; no scope expansion occurred.
The test-file LF hash after that selector-only correction is
`d83806d234f2120ef50c4ac4692d97a118839f96ef56c20122503a83d502e403`;
the table above records the exact state exercised by the initial invocation.

The next selector-only correction finds the receipt binding with `source_id`
`p0:1` and `kind` `shape`, selects its bound SVG element, and asserts that the
element is a rectangle. It retains receipt verification and makes no paint-order
or generated-ID assumption. Production code is unchanged. Independent read-only
review approved the fixture source ID, receipt keys, element lookup, and namespaced
rectangle assertion before execution. Its test-file LF SHA-256 is
`3eb615afe484301b69ce560e22c4737bbcab0eea22783e5f78466d3456d63662`.

The final authorized execution used the same selected command and baseline pin.
Result: exit **0**, **36 passed, 59 deselected** in **5.86 seconds** (shell elapsed
**7.237 seconds**). All **10/10 differential cases** passed alongside the repaired
regression and the pre-existing selected tests. Raw output is retained at
`docs/session-results/svg-source-preset-compatibility-20261108.pytest-final.log`.
The post-execution LF hashes were read again: layout
`21af8290309c7057affdb523730c7c22253207e3374aa43f1db4f9e1a67a8c29`,
preset helper `13da0920f0a031c75f42c2be3cce85c0544048ccd612ace5e9de3ab7040cac51`,
whole-page verifier `0e40404f21d6c8588fdbe6b39733710f44f2ffb01dc5657043b58b8bd0c5793a`,
and test file `3eb615afe484301b69ce560e22c4737bbcab0eea22783e5f78466d3456d63662`.
These match the reviewed/executed source state.

## Evidence limits

This bounded check does not establish full registry fidelity, Office rendering,
persisted original-PPT replay, or compatibility of other concurrent changes.
Partial/page-render helper inventory coverage and the provenance of registry
evaluation dependencies remain root-owned source-closure work. No reviewed
source-state mapping is authorized merely by this document.
