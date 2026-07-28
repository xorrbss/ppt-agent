# Upload limits

Presenton accepts larger editable PPTX files without relying on the internal
8 MB safety constants. User uploads are controlled by the following
server-side settings:

| Setting | Default | Hard cap | Applies to |
| --- | ---: | ---: | --- |
| `PRESENTON_MAX_UPLOAD_MB` | 100 MB | 512 MB | Each document, PDF, or PPTX |
| `PRESENTON_MAX_UPLOAD_TOTAL_MB` | 512 MB | 512 MB | Combined files in one supporting-document request |
| `PRESENTON_MAX_IMAGE_UPLOAD_MB` | 20 MB | 64 MB | Each uploaded PNG, JPEG, GIF, or WebP image |

All values are integer MiB. Missing, non-numeric, zero, or negative values use
the safe default; values above a hard cap are clamped. Configure the same values
for the FastAPI and Next.js processes. Docker Compose passes all three settings
to the combined service.

The UI obtains the effective values from `/api/upload-limits`. FastAPI also
exposes `/api/v1/ppt/files/upload-limits`. Exactly the configured file-byte
boundary is allowed; one byte over is rejected with HTTP 413 and a limit-specific
message. Uploads are written in 1 MiB chunks, unknown/chunked lengths are counted
while streaming, and partial files are removed after rejection. Image uploads
also require a permitted MIME type, extension, and matching file signature.

The nginx body ceiling is 520 MB. This is intentionally just above the 512 MB
application hard cap to leave room for multipart framing. `proxy_request_buffering
off` lets FastAPI enforce the application limit while streaming. Deployments
behind another proxy or ingress must set its body limit and timeouts consistently;
an upstream limit lower than the configured Presenton limit will reject first.

These limits protect request memory, temporary disk space, document conversion
time, and denial-of-service exposure. Raising them should be paired with capacity,
concurrency, and timeout review.

## Why some 8 MB constants remain

The 8 MB constants in authored-hybrid data-image validation and PPTX XML member
preflight are not user upload limits:

- Authored-hybrid HTML must be self-contained, so individual `data:` images are
  capped to prevent a generated page from expanding memory unexpectedly.
- PPTX package XML members are capped independently to resist ZIP bombs and
  pathological XML.
- Authored-style reference images sent to an LLM are capped separately to bound
  model request payloads.

Those security boundaries remain unchanged. Increasing them would not allow a
larger PPTX upload and would weaken defense in depth.

## HTTP boundary QA

Run the isolated end-to-end check from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File servers/nextjs/scripts/qa-upload-http-e2e.ps1
```

The script refuses to reuse occupied test ports, creates dedicated authentication,
application-data, and backend-temp directories below `.tmp-upload-http-e2e`, and
stops only the process tree that it started. It keeps authentication enabled and
logs in through the real session endpoint before checking:

- unauthenticated FastAPI and Next.js requests return `401`
- authenticated upload-limit metadata is available through both servers
- an exact single-file boundary is accepted
- one byte over the boundary is rejected with `413`
- a streamed request without `Content-Length` is rejected with `411`
- environment overrides are reflected by the API and enforced by upload routes
