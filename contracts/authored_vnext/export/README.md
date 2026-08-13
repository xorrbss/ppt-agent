# Authored vNext export contract

`v1/export-contract.schema.json` is the canonical closed wire contract shared by
FastAPI and the authenticated Next.js OOXML worker. It covers public job
admission/status/cancellation/rollback, public artifact receipts, and the
internal buffered worker request/response.

Generate checked-in Python and TypeScript types:

```powershell
uv run --project servers/fastapi python contracts/authored_vnext/export/generate.py
uv run --project servers/fastapi python contracts/authored_vnext/export/generate.py --check
```

Production code must never expose `BufferedArtifact.contentBase64`, a storage
key, or a filesystem path in a public response. Publication returns only the
opaque `ArtifactReceipt.artifactId` and same-origin `downloadUrl` after package
read-back and digest verification.
