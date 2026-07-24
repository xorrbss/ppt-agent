# Upstream compatibility control

This directory records the U0 compatibility contract against upstream commit
`57b194b234b42c8b28f8a507a30322de200e3e83`.

- `upstream-compatibility.json` pins application/export/frontend versions, the
  11 Template V2 renderer discriminators, the lossless upstream payload
  boundary, and key `/api/v1` endpoints.
- `migration-translation-ledger.json` records the upstream and local Alembic
  chains after their common revision, including explicit non-translations.
- `protected-local-patches.json` identifies authored-hybrid, Windows runtime
  sync, and export security behavior that must survive upstream rebases.
- `upstream-test-contracts.json` records each reviewed upstream default-template
  and generic async-task test, including machine-readable exclusion reasons and
  the non-conflicting local regression contracts retained in its place.

Run `node scripts/verify-upstream-compatibility.mjs` before and after an
upstream merge. The verifier is dependency-free and reports all detected drift
in one pass. Update a contract only when the related upstream or local design
decision is reviewed; do not silence drift by changing expected values alone.
