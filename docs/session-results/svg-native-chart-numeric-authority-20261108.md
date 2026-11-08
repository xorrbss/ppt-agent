# V5 native chart numeric authority — 2026-11-08

Status: implementation and focused regressions prepared for independent review.
No pytest, browser, provider, database or server was run by this task.

The free-SVG renderer's generic numeric-text requirement previously demanded
every chart datum as plaintext, even when the supported V5 native contract
explicitly permits numeric value labels to be hidden. The repair is restricted
to explicit V5 snapshots outside source-reflow/raster preservation paths.
Global fresh admission remains V4 unless its existing opt-in selects V5.

The existing successful chart parser now optionally records each data-value and
point-unit source span internally. It publishes no extra requested-data fields;
failed series never publish partial spans. Existing requested/native static
quality and geometry are checked in a separate scratch dictionary. Each actual
requested chart occurrence must consume a distinct matching declaration/proof.
Equivalent repeated requests can consume equivalent distinct proofs.

Only corresponding literal value occurrences in the existing numeric evidence
view are masked, before numeric deduplication. Category/series digits, notes,
axis parameters, scientific-grammar uncertainty and unrelated prose remain
under the original requirement. A repeated numeric string outside an authorized
span therefore remains required. Quality-policy V2 offsets are computed in its
existing source-content-evidence projection, rather than applying raw-source
offsets to a transformed string. Native source selection is shared with the
existing final gate: exact-count slides_markdown, otherwise the page content.

The final V5 gate still receives the same actual capture and rejects absent,
pending or false paint proof. No producer quality, source, capture or receipt is
restamped. Source/raster handling and older/unknown revision numeric behavior
retain their existing path.

The new focused test file covers all six families without numeric labels,
unbound/malformed/mismatched data and geometry, repeated chart/prose occurrences,
category/note digits, units/scientific uncertainty, policy-V2 projection, exact
source selection and the mandatory final paint gate. It also loads the actual
local Git HEAD parser under a temporary module alias, holding imports at current
bytes, and compares exact serialized requested-data bytes for 24 representative
unpinned/V2/V3/V4 cases. This is a pending module-differential check, not whole
checkout or export compatibility proof.

The previously accepted 104-row static legacy byte corpus and 166-pass paint
consumer run describe the source state before this helper integration. They are
not relabeled as fresh evidence for these edits. Current reviewed-ledger keys
affected by native_requirements/render are provisional until root freezes the
final sources, obtains independent review and runs the authorized focused check.

Root's first authorized focused run reported 50 passed and one failed. One
static helper diagnostic isolated the failure to the duplicate-chart fixture:
both declarations were bound, but the second chart's legend translated from
x=900/925 to x=1500/1525 outside the unchanged 1280-wide canvas. Its actual static
proof correctly returned `native_chart_content_off_canvas`; the numeric helper
therefore retained all original claims. The fixture now declares a 1920-wide
canvas and explicitly requires both distinct static proofs to pass before its
one-to-one numeric assertions. Production behavior and every guard are unchanged
by this fixture repair. No post-repair pytest or diagnostic call was performed.
