# OOXML vNext Office compatibility verification

These tools keep four different claims separate. A result in one lane must not
be promoted into a claim in another lane.

| Lane | What it can establish | What it cannot establish |
| --- | --- | --- |
| Microsoft Open XML SDK | Read-only package open and schema validation for `Microsoft365` | PowerPoint rendering, save behavior, or repair-dialog absence |
| PowerPoint desktop availability | Installed executable and file/product version | Successful reopen; the matrix never starts or attaches to PowerPoint |
| LibreOffice | Isolated headless conversion of a disposable copy and converted-package read-back | Byte preservation or PowerPoint repair behavior |
| Visible manual witness | Whether a human saw a repair dialog while visibly opening the disposable copy | Automated or unattended evidence |

## Exact-locked Open XML SDK validator

The validator is a small read-only .NET tool in `openxml-validator/`. It pins
`DocumentFormat.OpenXml` to exactly `3.5.1`; `packages.lock.json` records its
resolved dependency closure and NuGet content hashes. Restore fails closed in
locked mode if that closure drifts.

```powershell
cd servers/nextjs/lib/ooxml-vnext/tools/openxml-validator
dotnet restore --locked-mode
dotnet build --no-restore --configuration Release
dotnet .\bin\Release\net9.0\OpenXmlValidator.dll C:\path\to\candidate.pptx
```

The program opens `PresentationDocument` with editing disabled, computes the
input digest before and after, runs `OpenXmlValidator` with the
`Microsoft365` target, returns structured JSON, and reports at most 500 error
records while retaining the full error count. Exit codes are `0` for zero SDK
errors and an unchanged input, `2` for validation errors, and `1` for input or
tool failure.

## Compatibility matrix

Inventory only; this does not open the presentation in any external office app:

```powershell
.\office-compatibility-matrix.ps1
```

Run the exact-built Open XML SDK validator and isolated LibreOffice read-back:

```powershell
.\office-compatibility-matrix.ps1 `
  -InputPath C:\path\to\candidate.pptx `
  -RunOpenXmlSdk `
  -RunLibreOffice `
  -OutputJson C:\path\to\candidate.office-matrix.json
```

LibreOffice receives only a temporary disposable copy and a unique temporary
user profile. Its conversion output is package-read back, then the owned temp
directory is removed after path and reparse-point checks. The source digest is
checked after all requested lanes. Its receipt includes wall-clock, sampled
peak working/private memory, observed CPU, sample interval, and the new
LibreOffice process ids; any process ids present before the run are excluded.
No network provider is called.

The matrix intentionally has no PowerPoint automation mode. It does not launch,
attach to, save through, or terminate PowerPoint, even when PowerPoint is
installed or already running. Therefore `powerPointReadBack` always remains
`not-established` in this tool.

## Visible repair-dialog witness

Create a safe witness bundle without launching PowerPoint:

```powershell
.\office-compatibility-matrix.ps1 `
  -InputPath C:\path\to\candidate.pptx `
  -CreateManualWitness `
  -ManualWitnessRoot C:\path\to\disposable-witnesses
```

The created directory contains a digest-matched disposable copy and a
`manual-witness.json` template. A human must confirm the copy, open it visibly
in PowerPoint with macros disabled, record whether a repair dialog appeared,
avoid saving, and close only that presentation. Until those fields are filled,
the status remains `awaiting-human-witness`; the automated report never claims
`repair dialog = 0`.

The interactive runner additionally opens the source through a read-only file
handle with `FileShare.Read` for the full witness. That denies writes and
deletes, creates the disposable copy directly from the locked handle, verifies
the copy digest before launch, and derives the `/x` launch target from the
distinct disposable path. The receipt records those checks instead of using a
constant `openedByPowerPoint=false` assertion.

## Three-slide repair bisection

The checked-in 90-slide recipe is a preservation-negative fixture. Its
29-byte OLE sentinel was not constructed or validated as an OLE1/OLE2 embedded
object, its OLE relationship has no matching `p:oleObj r:id` consumer, and its
external hyperlink relationship has no matching hyperlink consumer. It must
not be used as the clean PowerPoint compatibility witness.

Create six small variants that isolate the product-generated package, custom
XML, external relationship, synthetic OLE part, orphan OLE relationship, and
the combined 90-slide additions:

```powershell
cd servers/nextjs
node --experimental-strip-types --import ./scripts/register-node-test-loader.mjs `
  ./lib/ooxml-vnext/fixtures/build-powerpoint-repair-bisection.mjs `
  ./.tmp/ooxml-repair-bisection
```

Run the exact-locked SDK validator on each file first. Then use
`01-clean-3.pptx` for the product witness. Only if it opens with no repair
dialog, use variants 02 through 06 in order to attribute the synthetic-fixture
repair. Schema validation alone cannot make that attribution.

The repository-contained interactive runner can prepare the same fail-closed
bundle, optionally run supplemental LibreOffice read-back, and only when no
PowerPoint process already exists start a visibly separate `/x` instance:

```powershell
.\powerpoint-visible-witness.ps1 `
  -InputPath C:\path\to\candidate.pptx `
  -WitnessRoot C:\path\to\disposable-witnesses `
  -RunLibreOffice

.\powerpoint-visible-witness.ps1 `
  -InputPath C:\path\to\candidate.pptx `
  -WitnessRoot C:\path\to\disposable-witnesses `
  -LaunchVisibleWitness

.\powerpoint-visible-witness.ps1 `
  -InputPath C:\path\to\candidate.pptx `
  -WitnessRoot C:\path\to\disposable-witnesses `
  -OutputJson C:\path\to\open-save-reopen-receipt.json `
  -LaunchOpenSaveReopenWitness
```

The first command never launches PowerPoint. The second is interactive, blocks
when an existing `POWERPNT` process is present, never attaches to or terminates
any process, never saves, and retains the disposable evidence bundle.
The third is an explicitly interactive open/save/reopen witness. It starts the
first disposable copy with `/x`, requires a human to record repair-dialog and
slide-count observations, make a visible body-text edit, and verify the company
master, cover/section/standard/long-title layouts, logo furniture, placeholder
mapping, page-number field, font manifest, and complex-text rendering before
saving in place and closing the process naturally. It
rechecks that no PowerPoint process exists before starting a second `/x`
process, then requires the same observations plus persisted editability on
reopen without another save.
The runner performs no COM call, automated save, attach, or process termination.
An unattended shell must not use either launch switch because it cannot close
the spawned window safely.

## Local availability observed on 2026-08-14

- PowerPoint desktop: available, `16.0.20228.20158`; deliberately not opened.
- Microsoft Open XML SDK: exact `3.5.1`, `Microsoft365` validation of the
  reproducible existing-style 90-slide fixture passed with zero errors and an
  unchanged source digest.
- LibreOffice: available, `26.2.5.2`; isolated conversion/read-back of that
  fixture passed with 90 slides and 195 parts. The sampled run took 6.9 seconds
  with a 275,894,272-byte peak working set and a 273,522,688-byte peak private
  memory footprint.
- Visible PowerPoint open/save/reopen witness: awaiting an interactive human;
  no PowerPoint process was launched by the unattended verification.

These are machine- and fixture-specific observations, not universal product
compatibility claims.

## Focused tests

From `servers/nextjs`:

```powershell
node --experimental-strip-types --import ./scripts/register-node-test-loader.mjs `
  --test lib/ooxml-vnext/tools/office-tools.test.mjs
```

The test checks lane separation, exact package locking, read-only SDK open, the
manual disposable-copy contract, and absence of PowerPoint COM/termination
primitives. It does not launch PowerPoint or LibreOffice.

