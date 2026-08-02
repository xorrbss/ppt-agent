# Template V2 catalog candidate preflight

Use this read-only command after rights evidence and an external approval have
been prepared, but before replacing the repository catalog manifests or
enabling a candidate for users. The command does not approve rights, generate
an attestation, change publication status, mutate the database, or modify the
retained PPTX.

## Deployment configuration

The production and production-gpu Compose services pass these operator-managed
values through from the deployment environment:

- `TEMPLATE_V2_RIGHTS_APPROVER_ALLOWLIST`: comma-separated exact reviewer
  identities controlled outside the repository.
- `TEMPLATE_V2_RIGHTS_ATTESTATION_KEY`: deployment secret with at least 32 UTF-8
  bytes.

Do not put the attestation key in a manifest, Compose file, command argument,
shell history, test fixture, or CI log. An empty/unset value fails closed. The
preflight output reports only whether a usable value is configured; it never
prints the value, reviewer identities, HMAC, or evidence-set digest.

The normal Template V2 rollout controls must also select the exact candidate:

```powershell
$env:ENABLE_TEMPLATE_V2 = "true"
$env:TEMPLATE_V2_TEMPLATE_ALLOWLIST = "<exact-template-id>"
$env:TEMPLATE_V2_DEPLOYMENT_TIER = "production"
```

Managed staging/production deployments require PostgreSQL and the current
Alembic head. Configure the database and `APP_DATA_DIRECTORY` exactly as the
service uses them before running the command.

## Vet a proposed manifest pair

Run from `servers/fastapi` in the same environment and against the same database
and app-data volume as the service:

```powershell
uv run python scripts/check_template_v2_catalog_candidate.py `
  --template-id "<exact-template-id>" `
  --catalog-manifest "<path-to-proposed-catalog.json>" `
  --rights-manifest "<path-to-proposed-rights.json>"
```

Omit the manifest arguments to inspect the repository manifests. Those
manifests intentionally remain blocked until every rights requirement in
`ppt-master-rights-publication-runbook.md` is satisfied, so the current command
is expected to exit 2.

Exit 0 and `template_v2_catalog_candidate_ready` require all of the following:

- a validated local catalog entry marked `published` and its exact rights row;
- complete digest-pinned evidence, trusted external approval, and valid HMAC;
- the exact candidate ID in the rollout allowlist and a safe database tier;
- the live database at the repository's exact Alembic head;
- one shared (unowned) database row for the catalog ID;
- valid native-clone publication evidence bound to the catalog/rights receipt;
- the retained PPTX at the canonical template/revision storage key, with exact
  byte size and SHA-256.

Any mismatch exits 2. Treat that result as a publication blocker; do not edit a
digest, approval, status, or stored receipt merely to make the command pass.

## Regression checks

```powershell
cd servers/fastapi
uv run pytest tests/unit/test_template_v2_catalog_candidate_preflight.py -q
uv run pytest tests/unit/test_template_v2_ppt_master_catalog.py -q
```
