# Authored vNext UI runbook

## Enable and open

The UI and FastAPI Master slice are default-off. Enable both processes for a
local validation run:

```powershell
$env:NEXT_PUBLIC_AUTHORED_VNEXT_MASTER_ENABLED="true"
$env:AUTHORED_VNEXT_MASTER_ENABLED="true"
```

Open `/authored-vnext`. The supported order is materials, Master Setup publish,
Visual Direction confirmation, ten-slide outline/layout, deck generation, and
the typed-command Workbench. `/authored-vnext/master-setup` remains available as
the standalone Master designer; pass a same-origin `continue` query parameter
when another flow owns continuation.

## Persistence contract

- Master draft, validate, publish, preview, deck creation, and body-only
  regeneration use `/api/v1/ppt/authored-vnext/master/*`.
- Workbench edits are stable-ID typed patches in an operation outbox. There is
  no whole-deck autosave and no wildcard slide mutation.
- Undo and redo enqueue inverse/forward patches; they do not replace the deck.
- A CAS conflict requires an explicit rebase decision. Last-known-good content
  remains visible in stale/error states.
- Renderer HTML is never canonical editable state and is never injected into
  the Workbench DOM.

## Current backend boundary

The current FastAPI slice does not expose typed patch dispatch or export-request
endpoints. The Workbench therefore keeps those operations visibly queued in the
client outbox, and the export CTA records a queued request effect only. Do not
present either as server-persisted until the corresponding backend contracts
land. The deck creation and body-only regeneration calls are real API calls.

## Focused verification

From `servers/nextjs`:

```powershell
npx tsc -p "app/(presentation-generator)/authored-vnext/tsconfig.check.json" --pretty false
node --experimental-strip-types --import ./scripts/register-node-test-loader.mjs --test "app/(presentation-generator)/authored-vnext/authored-vnext-route.test.mjs" "app/(presentation-generator)/authored-vnext/master-setup/master-setup-route.test.mjs" lib/authored-vnext/master/contracts.test.ts lib/authored-vnext/master/client.test.ts lib/authored-vnext/flow/feature-flag.test.ts lib/authored-vnext/flow/reducer.test.ts lib/authored-vnext/workbench/workbench.test.ts
npx cypress run --component --spec "app/**/authored-vnext/AuthoredVnextFlowClient.cy.tsx,components/authored-vnext/AuthoredWorkbench.cy.tsx"
```
