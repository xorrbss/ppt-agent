# Chat-first Workspace v1 contract fixtures

These fixtures are deliberately content-small, stable examples shared by backend
and frontend tests. They describe the persisted API contract rather than an
implementation-specific database representation.

- `bootstrap-and-assets.json` records the durable first user message,
  attachments, settings, idempotency receipt, and the asset wait policy.
- `timeline-projection.json` records the two independent cursors: pagination
  for top-level messages and high-watermark replay for one operation card.
- `commands-and-receipts.json` records command/action idempotency and
  optimistic revision/version expectations.
- `errors-and-runtime.json` records stable safe error codes and the supported
  runtime capabilities.

Do not add real prompts, customer filenames, URLs, auth material, or traces to
these fixtures. Schema changes require a new `vN` directory; a compatible
optional field may be added only with a matching invariant test in both
runtimes.
