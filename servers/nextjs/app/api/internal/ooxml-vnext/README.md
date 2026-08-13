# Authored vNext internal OOXML worker

This route is a server-to-server boundary, not a browser API.

- `POST /api/internal/ooxml-vnext`
- `Authorization: Bearer <AUTHORED_VNEXT_OOXML_WORKER_TOKEN>`
- `Content-Type: application/json`
- `AUTHORED_VNEXT_OOXML_WORKER_TOKEN` must contain at least 32 characters.
- `OOXML_VNEXT_INTERNAL_SECRET` remains a fallback alias for local transition only.
- Optional limits: `OOXML_VNEXT_MAX_BODY_BYTES` (default 4 MiB) and
  `OOXML_VNEXT_WORKER_TIMEOUT_MS` (default 30 seconds).

The `authored-vnext-ooxml-worker/v1` request contains `operationId`,
`fencingToken`, `master`, `slides`, `assets`, and lowercase raw SHA-256 digests
for the master JSON, slide-spec JSON, asset manifest JSON, font-manifest JSON,
and optionally the expected final PPTX bytes. JSON digests use recursively
sorted object keys, array order preservation, UTF-8, and no insignificant
whitespace.

The current adapter supports text placeholder values and rejects non-empty
`assets` after verifying their individual and manifest digests. This prevents a
caller from receiving a successful export that silently omitted a requested
asset.

The worker owns its temporary directory. It never accepts a destination path,
fully buffers the PPTX, reopens and validates the candidate through the OOXML
adapter, reads it back, deletes the temporary candidate, and returns base64
bytes plus artifact/package/preservation receipts. Publication CAS and the
last-known-good artifact pointer remain the FastAPI job owner's responsibility.
