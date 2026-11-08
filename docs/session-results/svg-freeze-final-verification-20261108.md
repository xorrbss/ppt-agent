# SVG freeze implementation and local verification — 2026-11-08

For the nine-page synthetic chart artifact, implementation and local verification
have reached normal publication, HTTP workspace export, and native PPTX
data/readback acceptance. **Final PowerPoint rendering of that artifact remains
pending** because pre-existing PowerPoint processes trigger the renderer's
ownership guard. This is bounded chart verification, not completion of the full
product request. Real template/product workflows, pictures, sizing, connectors
and measured quality acceptance still have the separate work listed in
[remaining SVG work](svg-work-remaining-20261108.md).

The managed application is running at <http://localhost:5000>. This report does
not represent a merge, deployment, paid quality review, or production acceptance.

## Scope and preservation

- All non-SVG modes, their dedicated tests, and their CI configuration are kept
  frozen. No tracked file was deleted. The SVG CI inventory retains all 478
  baseline paths and now contains 495 paths, with zero removals.
- V5 remains a fresh, explicit opt-in. The API and both controllers have no
  `SVG_NATIVE_CHART_INTEGRITY_V5` setting; the existing V4 fresh-admission default
  remains. Verification restores the genuine, previously pinned V5 snapshot.
- Existing local user-template settings were preserved. No global flag was
  enabled to obtain passing captures.
- Existing source-verifier keys, anchors, formulas, and accepted source states
  remain preserved. The current reviewed source state covers 156 direct files.
- Work was divided among implementation and independent review agents. Sol
  handled implementation/integration; Astra handled difficult contract work and
  independent source, interface, ledger, and regression reviews.

The full implementation history, component reports, and earlier failures are in
[the root integration journal](svg-root-integration-20261107.md). The controlling
scope is [the SVG freeze brief](../codex-brief-svg-freeze-quality-20261107.md).

## Actual chart captures

All nine cases passed real browser capture, native semantic requirements, and
the strict paint consumer with the original timeout policy: timeout omitted,
request timeout 60 seconds, client deadline 85 seconds.

| Case | Capture seconds | Result |
| --- | ---: | --- |
| Column | 60.89 | Passed |
| Coincident multi-series line | 63.42 | Passed |
| Stacked column | 79.09 | Passed |
| Donut, hole ratio 0.7 | 64.37 | Passed |
| Fixed-axis column with kg title | 63.93 | Passed |
| Pie with separators | 46.76 | Passed |
| Line with markers | 47.80 | Passed |
| Horizontal bar | 60.55 | Passed |
| Signed/zero multi-series area | 54.27 | Passed |

The unified manifest retains the exact same-source earlier donut capture and
the existing guarded absent-mode parity artifact; neither was relabelled as a
fresh request. All 49 capture source guards, helper guards, fixture guards, and
126 retained artifact hashes were independently checked.

Capture manifest SHA-256:
`7f193942624cd751e202842aa17c4179d685d60418d2ba5ecb807a0fb3c6f616`.
It is retained under the isolated `svg-chart-render-proof-1108` workspace at
`docs/session-results/svg-chart-render-proof-20261108/normal-profile-nine/acceptance-nine-native-composition-attempt-1/capture-manifest.json`.

The fixture is synthetic and covers all six existing chart families. This is
bounded runtime evidence, rather than an exhaustive claim about every possible
chart. In the area case, three earlier-series values are explicitly classified
as preserved but hidden by later owned data; the fully hidden value is not
claimed visually observable. The proof does not establish universal physical
exposure, unilateral visibility, or per-cluster font attribution.

## Publication and real server export

The original capture fields were preserved while constructing the publication
input. Only the downstream derived verification, classification, and requested
native-object fields were recomputed by the actual strict checks. The strict
consumer and publication preflight accepted all nine pages.
The assembled manifest SHA-256 is
`83e32d615918fa020cc2893c144b308af1d97fe066d8c68baf3936620ac4ce29`.

Normal publication initially exposed an existing new-document ordering defect:
the first database flush preceded the SVG identity stamp. The production fix
stamps the newly constructed SVG row before its first flush, retaining the
existing later stamp and all normal owner/account/publication checks. The
focused regression invokes the real publication function and real profile
validator at the first flush. It passed after its test wrapper was adapted to
the repository's installed async execution facilities.

The fixed normal publication completed at revision 1 with durable STARTED and
completion audit records. No job rows, admission result, owner identity, or
release seals were fabricated. The publication-order file is outside the 156
verifier source files and the capture/assembly source sets; independent review
confirmed that this fix requires no source-ledger repin or new chart capture.

A single ordinary `ExportPPTX` command was then submitted through the running
workspace HTTP API using the actual published revision. The matched operation
reached `succeeded`, and the artifact was downloaded and bound to its advertised
SHA-256 and size.

- Actual PPTX: 148,200 bytes; SHA-256
  `e3b953f8c13b293d3f668c5c8c58f8d9203e3daf8878bd69abe83d25f2a5589e`.
- Structural readback: nine slides, nine offered/native chart objects, zero
  image fallbacks, embedded Noto Sans KR.
- Independent native audit: **9/9 passed** against the original pre-capture
  fixture, including series/categories/order, signed and zero values, embedded
  workbook formulas/cells and chart caches, donut hole 70, fixed axes, stacking,
  orientation, markers, and effective per-point value-label visibility.
- The original native audit's three false negatives are retained. The exporter
  writes point-level label flags; the corrected audit checks every expected
  point with strict indices, boolean fields, deletion, and inheritance checks.
  The same unchanged PPTX then passed 9/9. Production export bytes were untouched.

The local PPTX and private receipts are retained in
`app_data/svg-freeze-runtime-20261108/workspace-export-native-composition-attempt-1/`.
Owner, tenant, operation identifiers, and auth/environment material are kept
outside this report.

The unsupported bare workspace-presentation GET route returns 404 even for a
published presentation. That earlier 404 is not evidence of row absence or
ownership. The supported timeline returned HTTP 200 at revision 1; the normal
command, terminal operation, and downloaded artifact provide the current export
evidence.

## Checks and running stack

| Check | Actual result |
| --- | --- |
| Producer carrier and composition support | 120 passed |
| Consumer and composition tests | 211 passed; nine artifact cases initially skipped |
| Those nine genuine-artifact cases, run separately with their manifest | 9 passed, 157 deselected |
| V5 marker static visibility | 8 passed |
| Current verifier source ledger | 58 passed, one existing conditional skip |
| New-document first-flush regression | 1 passed in 6.45 seconds |
| Actual chart capture and strict consumer | 9 passed |
| Independent actual native PPTX audit | 9 passed |

These are separate scoped checks, not a combined full-suite result. The first
new regression attempt stopped before its body because an async pytest plugin
was unavailable; changing only the new test to `asyncio.run` allowed the same
node to execute successfully. No dependency or pytest configuration was added.

Managed reloads affected only the API and relevant operation controllers.
Next.js, the proxy, the existing PostgreSQL process, ports, and launch settings
were preserved. After publication and export, proxy root, auth status, Next.js
health, API health, and API readiness all returned HTTP 200. All three backend
components retain PostgreSQL configuration, and Next.js retains USER_CONFIG_PATH.

This local stack uses its existing local authenticated-admin configuration.
These checks do not establish adversarial tenant/RLS coverage. No full non-SVG
behavioral suite or provider-backed prompt-to-generation HTTP run was performed.

## Remaining Office verification for this chart artifact

The provider-free existing `render_final_pptx` path was invoked against the
actual downloaded PPTX. It returned
`svg_final_pptx_office_instance_busy` before COM activation, in approximately
0.5 seconds. The PPTX hash remained unchanged.

The renderer deliberately refuses pre-existing PowerPoint processes and later
requires exclusive, proved ownership of its hidden process. Existing user or
unknown PowerPoint processes were not closed, killed, reused, or bypassed. The
user has been asked to save and close PowerPoint, or to defer this last check.

Once a clean Office environment is available, the remaining work is to render
all nine actual PPTX pages with the unchanged owned-process renderer, retain the
hash-bound PNGs and renderer/version evidence, and inspect the output. Office
visual fidelity, separator appearance, and the retained kg title's position are
still unverified. No final visual-acceptance seal has been issued.

This closes only the Office gap for this synthetic chart artifact. The broader
real-template workflows, template pictures, content sizing, connectors experiment,
paid quality measurement and relevant frozen-mode checks remain separately
tracked in [remaining SVG work](svg-work-remaining-20261108.md); Office completion
alone does not finish the original product-level request.

The machine-readable evidence index omits owner, tenant, and auth identifiers:
[the Office-pending verification record](svg-runtime-final-nine-20261108-office-pending.json).
