# OOXML package audit CLI

`ooxml-audit.mjs` is an isolated, read-only PPTX/PPTM package audit harness. It
uses only Node.js standard modules, performs no network I/O, and never extracts
ZIP entries to disk. Input paths are reduced to basenames in JSON reports.

## Commands

```powershell
node .\ooxml-audit.mjs inspect .\before.pptx --pretty --output .\before.audit.json

node .\ooxml-audit.mjs compare .\before.pptx .\after.pptx `
  --allow-text-change 'ppt/slides/slide7.xml' `
  --fail-on-violation --pretty --output .\roundtrip.audit.json
```

Report files are create-only. An existing report is overwritten only when the
caller supplies `--force`. `--fail-on-violation` returns exit code 2 when a
comparison violates an invariant; malformed, unsafe, encrypted, unsupported, or
corrupt ZIP input returns exit code 1.

`--allow-text-change` is the gate for a targeted text edit. It permits a matched
XML part only when the before/after byte strings are identical after replacing
the content of every `<a:t>` element with an ordinal placeholder. A newly
materialized `b`/`i` attribute, whitespace normalization, timing/unknown-node
damage, tag change, or non-text mutation is classified as
`xml-structural-or-formatting` and fails the invariants. Text values are not
written to the report; exact changed-region and per-text-node byte counts and
SHA-256 values are reported instead.

`--allow-change` is a broader part-level escape hatch for a separately justified
rewrite. Both options accept an exact OPC part name or a glob. `*` stays within
one path segment, `**` crosses segments, and `?` matches one non-slash character.
Allowed changes remain visible in `partDiff.allowedObservedChanges`; neither
option permits removed parts or relationship-closure changes.

## JSON contract

Every report has `schemaVersion: "ooxml-audit/v1"` and a `mode` of `inspect` or
`compare`.

An inspection report contains:

- uncompressed SHA-256 and CRC-32 per OPC part, plus a stable aggregate payload
  hash;
- parsed content-type defaults and overrides;
- canonical internal/external relationship records, reachable-part closure, and
  missing source/target evidence;
- slide, master, layout, media, orphan, and unknown-part inventory;
- evidence for macros, external relationships, SmartArt, OLE/ActiveX,
  animations, comments, and custom XML.

A comparison report uses uncompressed part hashes. ZIP timestamps, entry order,
and compression changes therefore do not count as payload changes. It reports
all added, removed, changed, allowed, and unrelated parts plus exact canonical
relationship and reachable-closure set differences. `invariants.pass` requires:

- `missingPartCount === 0`;
- `untouchedPayloadChangeCount === 0`;
- `relationshipClosureChangeCount === 0`;
- no unresolved relationship targets after the operation; and
- a valid package read-back after the operation.

## Fail-closed limits

The harness rejects encrypted entries, duplicate entry names, unsafe or
non-canonical entry paths, multi-disk ZIPs, ZIP64, unsupported compression,
central/local header mismatches, bad CRC-32, entries over 512 MiB, and packages
claiming over 2 GiB uncompressed. XML evidence scanning is capped at 32 MiB per
part. Stored and raw-deflate ZIP entries are accepted.

Run the synthetic focused checks with:

```powershell
node --test .\ooxml-audit.test.mjs
```

The tests create fixtures only under the operating system temporary directory;
they do not use or transmit customer files.
