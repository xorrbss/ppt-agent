# Authored Hybrid evidence retention

`config/authored-hybrid-evidence-retention.json` is the project policy for the
immutable evidence bundle made by each Hybrid PPTX attempt. The bundle binds one
attempt ID, presentation revision, canonical and renderer source digests, PPTX
digest, coverage manifest digest, render manifest plus per-slide PNG digests,
and report manifest/`visualParity` digest. `evidenceDigest` is the SHA-256 of
the canonical bundle payload without the digest field itself.

The browser history projection must show missing evidence as `incomplete`, an
elapsed evidence window as `expired`, and `visualParity=not-verified` exactly
as such. A native count or a retained backplate is not visual-parity proof.
Typography collection/fallback reasons are displayed when recorded; their
absence is not converted into a successful font claim.

The current policy retains attempt metadata for 180 days and evidence bundles
for 30 days. Expiry is `mark_expired_retain_metadata`: this repository command
never deletes, overwrites, or prunes an existing artifact. Physical deletion
requires a separate, approved maintenance change with its own inventory and
recovery procedure. CI keeps policy-test output for 30 days; CI retention never
authorizes production evidence deletion.

Run the no-write validator against a copied/available manifest:

```powershell
node scripts/authored-hybrid-evidence-retention.mjs path\to\bundle.json
```

The response is a retention decision (`retained` or `expired`) and always sets
`deletionAuthorized=false`.
