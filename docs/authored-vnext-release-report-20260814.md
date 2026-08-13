# Authored-only vNext release gate report

Date: 2026-08-14 KST  
Source baseline: `main` at `b387a34735482d4f6dfbf2b955fbf19fd0d2315e`; release changes are integrated on `codex/authored-vnext-release`  
Decision: **NO-GO — Windows PowerPoint visible witness pending**  
Rollout: all authored-only vNext feature flags remain default OFF. This report establishes enable-readiness only; it does not enable rollout.

## Final machine-verifiable gates

| Gate | Command or evidence | Result |
|---|---|---|
| FastAPI lint | `cd servers/fastapi; uv run ruff check .` | PASS |
| FastAPI full regression | `cd servers/fastapi; uv run pytest -q` | PASS: 3,374 passed, 18 skipped, 0 failed, 3 warnings in 478.20s |
| FastAPI raw receipt | `.codex-test-artifacts/final-fastapi-suite-20260814-rerun.log` | SHA-256 `4F23D2AB13C7621BA9BD22B319A022E47F958C815A00801ECD25035828B370CF` |
| Next typecheck | `cd servers/nextjs; npx tsc --noEmit --pretty false` | PASS |
| Next Node CI | `npm --prefix servers/nextjs run test:ci-node` | PASS: 985 tests, 984 passed, 1 platform skip, 0 failed |
| Next raw receipt | `.codex-test-artifacts/final-next-ci-node-20260814-rerun.log` | SHA-256 `9285d8ea6326ab79f9e2a13df99ebed58a80f17c5d40a04d7504193fbeafdba0` |
| i18n | `npm --prefix servers/nextjs run check:i18n` | PASS: 1,717 frozen fingerprints |
| File-size ratchet | `npm run test:file-size-ratchet` | PASS; no baseline increase or new exemption |
| Required CI contract | `node --test scripts/required-ci-contract.test.mjs` | PASS: 27/27 |
| Generated contracts | authored master/deck/export/OOXML and Template V2/TemplatePackage `--check` | PASS; drift 0 |
| Alembic | `uv run alembic -c alembic.ini heads` | one head: `b6d8e0f2a4c7` |
| Live UI/API E2E | `npm run test:workspace-e2e` | PASS: canonical route 1/1, live backend 1/1, authored 90-slide flow 1/1; 128s |
| Live E2E receipt | `.codex-test-artifacts/final-workspace-e2e-20260814.log` | SHA-256 `9297D97D5E03B6E495D74B423074732833AFE2BF0279B2E03A4EF97E5B2EE2D3` |
| OOXML 90-slide fixture | `servers/nextjs/lib/ooxml-vnext/fixtures/evidence/existing-90.build-receipt.json` | 90 slides, 4 layouts, 200 parts; archive SHA-256 `76236ccfaed176966dabcfb8d91b4541b17e8f6907606240d1e09a82bb78afa3` |
| OOXML/Office structural validation | Node OOXML 63/63; Open XML SDK 3.5.1; isolated LibreOffice 26.2.5.2 | PASS; SDK errors 0; LO 90-slide read-back; source unchanged |
| PostgreSQL 16 matrix | disposable PostgreSQL 16.15 | PASS: fresh, populated legacy, downgrade/upgrade, two-process migration and durable admission, active-write backup, physical read-back, dump/restore |
| Renderer OCI gate | `checkpoint-7-final-frozen2-{cold,identical-1,identical-2}` | each 72/72, failed 0, inconclusive 0, `promotion_allowed=true` |
| Packaged Electron renderer | `checkpoint-7-final-frozen2-electron` | 17/17 observed controls true, errors 0 |
| Package builds/preflight | Next build, FastAPI PyInstaller build, Electron TS/provenance, `npm --prefix electron run preflight:package` | PASS |
| NOTICE/SBOM/license | freshness and fail-closed license policy | PASS: 1,066 packages, declared/concluded `NOASSERTION` 0 |
| Production audit | root, Next, Electron | 0 production vulnerabilities; 3 Electron dev/build-only high findings remain visible |
| GenOffice/PPTist boundary | release compliance tests | PASS: 9/9; permitted GenOffice core only; PPTist/AGPL ingress absent |
| Upstream parity | working-tree parity verifier and self-test | PASS after final manifest regeneration; 9,459 identical, 704 reviewed exceptions, 2,700 local-only; self-test 6/6. The generated manifest is the canonical digest-bearing receipt. |

## E2E path actually exercised

The final authored live route used the public UI and FastAPI routes with disposable SQLite and local worker processes. It covered prompt entry, pre-generation Master Designer, checked-in logo upload, fonts, eyebrow/title/note/callout sizing, divider, page number and colors, publishing cover/section/standard/long-title layouts, 90-slide outline and generation, virtual rail navigation, reorder, body edit, undo/redo, regeneration reject/retry/accept, deterministic stale-CAS rebase and discard, rollback, real worker PPTX export, existing 90-slide PPTX import, explicit corporate normalization, preserve/no-op save, targeted edit, save/read-back and rollback. No external provider or customer credential was used.

## Renderer and package attestations

- OCI image: `sha256:29c56ae1e354336e9f6f6fe70c922454f42bb53c550580485cc0f9a5eddce8ed`
- Renderer source: `sha256:5cf31ce56dd43680d6014e3cfe84e4283b6a490d96ed9302f45ba28a0698c9ba`
- Renderer contract: `sha256:f44429b9f9fea987620a698b987fd6c65a58592fc69c63bf343fe8123292bba0`
- Electron executable: `c304ce81fa38e6ca80e3884dfe656f6f5e0cc48f53d5ab99560e7a88f33302dc`
- Electron package observation: `13d89862d53e3aa01b5bbdfb9133b1349b892b9386b1fc4feff87a54fad0fb16`
- Packaged source digests: Electron provenance manifest `1a44d8d497f685f0bcdd1b9c85fa9dceaf56f06dfa04db30846afbcee7de1de4`, Next source `ea292df999447305465c10ca0bef3834ff2549640dbdcb5bab7dde93fa6acdc3`, FastAPI source `e454e61c9ad52f77cf8426dec60e03576db6fc13b0a54fe32dd5bb062bdd75c6`
- Release SBOM: `56776da7fb8854c6f1e2aefca076e33d97b3e0891e97b6c001a1721b2b35f196`; NOTICE: `be3072d19c3ebc98cbe2def671789a60ef38fed8b6f208190b8f3a42f91b7713`

All three final OCI runs observed OOM/OOM-kill, PID max events and Chromium `EAGAIN` at zero. Memory-limit events were attributable to clean file-backed cache with direct reclaim, not anonymous growth. Sandbox, site isolation, network-none, cgroup limits and the 20-second browser bound were not weakened.

## PostgreSQL 16 evidence

The canonical immutable physical backup is `.codex-test-artifacts/pg16-fastapi-checkpoint-1-5-20260814/immutable-basebackup-20260814`; do not cite the older mutated `basebackup` directory.

- `pg_verifybackup`: PASS
- backup manifest SHA-256: `349e953d205f700316f913463435377edbb7a295fb48ec753ead6a7fa4023579`
- raw tree SHA-256: `16aa4b8e5df060bf56353a4b36a608485c00d83df903afe99735d1eb61594615`
- writer final: 1,000 rows, digest `666e6696ee9db2d2279f790744998f8f`
- clone snapshot: 587 rows, digest `82cef9f089abafd1b3e05ff4e4699dd4`, head `b6d8e0f2a4c7`
- custom dump SHA-256: `06179B838AABEC36CFFB814BB29DF2B37FCA79173AD9CB8D153371E19637EC18`
- disposable ports 55436 and 55438 were stopped and returned no response after verification.

## Open blocker

**Windows PowerPoint visible open/save/reopen witness is not established.** Open XML SDK and LibreOffice are supporting validators, but Microsoft PowerPoint remains the final oracle. The current session deliberately did not launch, attach to, automate, save through, or terminate PowerPoint because a human must observe repair dialogs and close both isolated `/x` processes naturally.

From the repository root, run:

```powershell
.\servers\nextjs\lib\ooxml-vnext\tools\powerpoint-visible-witness.ps1 `
  -InputPath .\servers\nextjs\.tmp\ooxml-vnext-checkpoint4-existing-90\existing-style-90.pptx `
  -WitnessRoot .\servers\nextjs\.tmp\ooxml-vnext-checkpoint4-existing-90 `
  -OutputJson .\servers\nextjs\.tmp\ooxml-vnext-checkpoint4-existing-90\open-save-reopen-receipt.json `
  -LaunchOpenSaveReopenWitness
```

The human witness must record repair-dialog count 0, 90 slides, four real layouts, company master/logo, placeholder mapping, page-number field, font/complex-text rendering, a visible edit, save, natural close, isolated reopen and persisted editability. Until that receipt passes, promotion is denied and all feature flags remain OFF.
