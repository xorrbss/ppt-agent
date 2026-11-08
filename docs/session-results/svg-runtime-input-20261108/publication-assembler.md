# Provider-free publication input assembler

`assemble_publication_inputs.py` is a new standalone preparation helper. It
has **not been executed**. It does not import an HTTP client, resolve auth,
open a database, generate content, capture a browser page, publish, export,
render PowerPoint, or invoke a provider. Root must review the helper and the
complete actual nine-case captures before running it with backend Python.
No production code or the reviewed `scripts/svg_freeze_runtime_replay.py` was
changed for this assembler.

## Actual inputs required

Use the real `nine-page-runtime-pin/` request, outline, style snapshot,
capture context, independent requested sources and `pin-provenance.json`.
The authored fixture is currently the renderer worktree artifact
`docs/session-results/svg-chart-render-proof-20261108/normal-profile-nine/authored/fixtures.json`,
SHA-256 `c05f2c0253ac2c7c97856fa625c00c13af1c61bdde442b8eda581073e61a917c`,
schema `normal-profile-nine-chart-fixtures/v1`. Its existence is source
preparation evidence; there are no accepted captures at assembler authoring.

The assembler takes `--capture-index PATH --output NEW_DIRECTORY`. The output
directory must not already exist. A failed attempt is kept as unready and must
not be reused as a completed input. This is local deterministic assembly only;
root separately invokes the reviewed replay validator or explicit publisher.

The immutable digest index has schema `svg-publication-assembly-input/v1` and
these required fields:

| Field | Required evidence |
| --- | --- |
| synthetic | literal true |
| capturesComplete | literal true, checked again through all nine actual inputs |
| pendingCases | empty list |
| caseOrder | exact pinned order of all nine cases |
| runtimeHarnessSha256 | root-reviewed runtime replay script raw SHA-256 |
| pinProvenance | exact file reference to actual pin-provenance.json |
| fixture | exact file reference to actual authored nine-case fixture |
| codeDigests | actual capture/consumer root-relative source identities; includes reviewed replay CODE_INPUTS |
| pages | nine entries in exact pinned case order |

Every file reference is exactly `{path, sha256}`, with raw 64-character
lowercase SHA-256. Input paths may be absolute or relative to the input index.
The assembled replay manifest uses the unchanged validator's prefixed
`sha256:<64hex>` representation for its references, code digests and pinned
request digest; the assembly input index continues to use raw hashes.
Each page requires `fixtureName` and references named `svg`, `payload`,
`capture`, `producerQuality`, `png`, `consumer`, and `witnessManifest`.
`payload` is the actual page dictionary used with that exact finalized SVG,
native declarations and pinned readability policy. Its existing quality must
retain every original non-derived producer field. The helper does not invent
generation attempts, receipts, jobs, reviews, seals or missing payload facts.

The original capture is the actual lossless four-field SvgRenderResult carrier:
`type="services.svg_renderer.SvgRenderResult"`, `pngBase64`, `quality`,
`wrapper_html`, `source_digest`; an existing schema tag is allowed. Its PNG
must equal the separately hashed PNG bytes and its quality must equal the
separately hashed original producer-quality JSON. Do not replace the
wrapper/source digests or use a result reconstructed from a preview.

The renderer team's planned `normal-profile-nine-capture-manifest/v1` remains
the capture receipt. The small digest index above can be emitted alongside it
by the root-reviewed capture preparation path. Its exact final aggregate
format and actual page-payload paths are pending renderer coordination. A
capture aggregate alone cannot be passed off as this strict digest index.

## Derivation and fail-closed validation

The original producer quality must contain all 17 reviewed mandatory fields:
contract_version, renderer_version, source_digest, png_digest, wrapper_digest,
metrics_digest, measurement_complete, geometry_passed, passed, verified,
semantic_review, failures, body_font_floor_px, browser_metrics, overlap_basis,
trusted_font_materialization, physical_readability. Original passed,
geometry_passed and measurement_complete must be true and failures empty.
The real readability validator runs on the preserved original quality; the
reviewed replay validator also checks exact frozen floor, trusted materialized
fonts, all capture digests and original browser metrics.

Only these three fields may differ in the derived page quality: `verified`,
`classification`, `requestedNativeObjects`. They are discarded and computed
again by actual `apply_requested_native_quality` and `reclassify_pages`.
Every other producer key/value is checked bidirectionally before and after
derivation and classification. No floor, font, passed flag, measurement flag,
metrics, digests, failures, or optional producer facts are filled or rewritten.

For each case the real bound-declaration reader and V5 witness builder must
match the authored fixture and supplied witness bytes; the actual strict paint
consumer must pass and equal its retained original receipt. The independent
requested source must equal the actual pinned source and typed outline.
Original pin code identities and actual capture/consumer code identities must
still match current root bytes. Changed pin code requires a genuine new pin
decision; the assembler does not silently restamp it.

The helper maps exact existing case facts into the replay validator's
`svg-chart-support-graph-examples/v1` fixture shape. It builds derived page
payloads and the deck summary through real quality functions, copies original
input bytes into the new output directory, then calls the unchanged reviewed
`validate_inputs` on `unvalidated-manifest.json`. Only a successful actual
validation permits the final rename to `replay-manifest.json`.

Outputs retain original capture, PNG, original producer quality, original page
payload, witness, strict consumer receipt, actual pin provenance, original
authored fixture and original digest index. `assembly-provenance.json` binds
the helper/reviewed validator/current consumer source hashes and the actual
validated replay provenance. It explicitly records no publication, provider,
capture or generation-HTTP execution. Stdout exposes only assembly status,
page count and final manifest digest; failure never becomes a passing receipt.
The assembler identity is captured at assembly start. That unchanged identity,
the pinned reviewed runtime-harness identity, and exact pending manifest bytes
are rechecked immediately before promotion; a later source edit is never
recorded as though it performed the earlier derivation.

The final ready manifest is consumed unchanged by
`scripts/svg_freeze_runtime_replay.py --mode validate-only --manifest PATH`;
publication still requires root's separate explicit normal-auth/RLS execution.
Normal publication guards and frozen quality policy remain authoritative.

## Pending evidence

All nine actual normal-profile captures, original page-payload artifact paths,
positive strict consumer receipts, immutable aggregate/index and current root
capture code identities are still required. No helper execution or new check
has been performed. Parent/root owns review and the single eventual narrow
execution after the actual nine-case evidence is accepted.
