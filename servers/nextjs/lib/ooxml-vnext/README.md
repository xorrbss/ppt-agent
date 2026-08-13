# OOXML vNext master adapter

This package is the narrow product adapter/compiler selected after the
`03-ooxml-roundtrip` experiment. It owns package inspection and preservation,
native master/layout/theme compilation, slide materialization, exact text-slice
patching, validation, and buffered atomic publication.

## Product boundary

- `createOoxmlMasterCompilerPort()` exposes opaque document handles and detached
  receipts. It never returns a mutable archive or a GenOffice engine object.
- Imported parts start as `PRESERVE_EXACT`. Only an approved text part becomes
  `PATCH_SLICE`; compiler-owned parts are `GENERATED`; macro-bearing packages
  are entirely `QUARANTINED`.
- Import retains a detached immutable copy of the original archive, a complete
  part inventory, and a digest-bound preservation manifest. Publication calls
  `assertPackagePreservation`; missing, changed, or newly added parts outside
  explicit `PATCH_SLICE`/`GENERATED` authorization fail closed.
- Imported masters and unsupported objects stay opaque. The adapter will not
  normalize or silently rebuild them.
- `inventoryMasters()` reads every imported master/layout relationship and
  PowerPoint id. `captureCorporateMaster()` returns an immutable byte-exact
  relationship-closure bundle; its publication helper rejects remapping,
  topology drift, payload tampering, and non-identical collisions.
- Text mutation copies every byte outside one selected `<a:t>` content slice.
  Fields, cross-run matches, ambiguous selectors, encoding changes, and required
  formatting changes are rejected.
- Publication accepts buffered bytes only and follows candidate write, package
  reopen, digest verification, fsync where supported, atomic rename, and final
  read-back. No streaming save API exists.
- GenOffice is separately available only as an exact-pin, compliance-gated,
  buffered no-op lab oracle. Text mutation, streaming, and master/layout CUD
  capabilities are permanently false. PPTist/AGPL and GenOffice `ee`, apps, AI,
  Electron, and sibling packages fail closed.

## Third-party fail-closed boundary

The optional GenOffice oracle is rejected unless all of the following match the
recorded evidence exactly: official repository and full commit SHA, pin digest,
Apache-2.0 component identity, byte digests for both `LICENSE` and `NOTICE`, the
SPDX 2.3 document digest/namespace/describes fields, the exact 22-package and
25-relationship production closure, the two runtime dependencies, the 37-file
source inventory digest, and the 17-check intake receipt. Missing or malformed
evidence is a rejection, never an allow-with-warning result.

Materialized paths are restricted to the retained license/build metadata and
`packages/pptx-engine`. `ee`, apps, AI, Electron, PPTist, sibling packages,
path escapes, unapproved dynamic imports, and any SBOM package declaring AGPL
fail closed. Passing this gate permits only the optional buffered no-op lab
oracle; it does not approve GenOffice as a production compiler or release the
adapter from its independent preservation checks.

## Focused checks

From `servers/nextjs`:

```powershell
node --experimental-strip-types --import ./scripts/register-node-test-loader.mjs `
  --test lib/ooxml-vnext/*.test.mjs

node ./lib/ooxml-vnext/cli.mjs inspect C:\path\to\deck.pptx
node ./lib/ooxml-vnext/cli.mjs noop C:\path\to\input.pptx C:\path\to\output.pptx
node ./lib/ooxml-vnext/cli.mjs generate-demo C:\path\to\demo.pptx 4

# Availability only; never launches or attaches to PowerPoint.
.\lib\ooxml-vnext\tools\office-compatibility-matrix.ps1
```

`noop` publishes a new file and requires that the output path does not exist.
The CLI never invokes GenOffice, PowerPoint, LibreOffice, or a network provider.

The retained 03 experiment's PowerPoint hidden COM reopen verified read-only
open and slide count but could not observe a visible repair dialog. A
`repair dialog = 0` claim still needs a manual disposable-copy witness.

For the non-destructive, four-lane Office verification workflow and exact-locked
Microsoft Open XML SDK validator, see `tools/README.md`. The current product
tool does not perform hidden COM reopen: PowerPoint availability, Open XML SDK
schema validation, LibreOffice read-back, and the visible human repair-dialog
witness are reported as separate claims.

## Product-port contracts

The adapter port exposes only opaque document handles and detached DTOs:

- `importPackage` / `readOriginalPackage` / `readBack`
- `inventoryMasters` / `captureCorporateMaster` / `importCorporateMaster`
- generated master/layout create-update-delete through `createGenerated`,
  `compileMaster`, and `deleteMaster`
- `materializeSlide` and one exact imported-part `targetedTextOperation`
- `saveBuffered`; there is intentionally no streaming publication method
