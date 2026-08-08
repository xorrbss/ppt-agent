# Chat-first Workspace v1 contract fixtures

These fixtures are deliberately content-small, stable wire examples shared by
backend and frontend tests. Backend parity tests validate every named request or
response example against its Pydantic model and the generated OpenAPI schema;
frontend parity tests pass the same timelines, event pages, and receipts through
the production normalizers. They do not describe a database representation.

- `bootstrap-and-assets.json` contains exact bootstrap request, asset response,
  and receipt-envelope response examples, including an immutable attachment
  snapshot and its separately projected live state.
- `timeline-projection.json` contains exact timeline and event-page responses.
  `next_before_position` paginates anchors; canonical decimal
  `event_high_watermark` values replay durable events independently.
- `commands-and-receipts.json` contains exact command/action requests and both
  first-delivery and duplicate action receipt envelopes. Flattened timeline
  fields remain byte-for-byte compatible with the nested v1 timeline.
- `errors-and-runtime.json` contains exact read/write capability responses, the
  central public error registry projection, and closed privacy field sets.

The directory-level `schema_version` labels the fixture family. Nested
`schema_version` fields are the actual v1 wire values and must remain strict.
Do not add real prompts, customer filenames, external URLs, auth material, or
traces. Schema changes require a new `vN` directory; a compatible optional field
may be added only with matching Pydantic/OpenAPI and frontend-normalizer tests.
