# Template V2 bounded table operations contract

`servers/nextjs/lib/template-v2-table-operations.ts` implements a pure
preview/apply boundary for structural table edits. It never writes templates,
uploads assets, or calls an external provider. Callers apply the returned
single-element replacement through the existing Studio reducer so undo/redo,
the local revision journal, autosave, and server `expected_revision` CAS remain
authoritative.

Supported structural previews are row/column insertion, deletion and reorder,
first-row/header conversion, and transpose. Existing cells move as complete
objects. New or imported cells clone the nearest cell prototype, replace only
run text, and retain cell style, run style, and unknown metadata. The complete
table object is spread into the result, retaining table-level metadata.

CSV/TSV import and paste are quote-aware and fail closed on ragged data,
malformed quoting, out-of-bounds paste, oversized input, oversized cells, or
declared/hard row, column, and cell-count limits. Limits are intentionally
local and synchronous: 200 body rows, 32 columns, 4,096 body cells, 4,000
characters per cell, and 1,000,000 input characters.

Every operation returns before/after digests and a deterministic diff.
Application rejects changed source tables as stale and mutated previews as
tampered. Table-to-chart conversion has its own preview/apply contract, accepts
only strict chart types, requires finite numeric value cells, and emits only
strict Template V2 chart fields. Pie/donut conversion requires exactly one
numeric series.

Long tables produce only deterministic row segment suggestions with a repeated
header marker. They do not clone presentations or slides; slide creation and
revision ownership remain with the caller.
