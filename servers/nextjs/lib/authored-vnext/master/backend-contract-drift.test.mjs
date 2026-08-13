import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import {
  AUTHORED_VNEXT_MASTER_API_PREFIX,
  AUTHORED_VNEXT_MASTER_ENDPOINTS,
  MASTER_CONTRACT_KEY_MANIFEST,
} from "./contracts.ts";

const contractDir = path.resolve(
  process.cwd(),
  "..",
  "..",
  "contracts",
  "authored_vnext",
  "master",
  "v1",
);

function contract(name) {
  return JSON.parse(readFileSync(path.join(contractDir, name), "utf8"));
}

test("frontend wire contract consumes the generated Pydantic schemas and API manifest", () => {
  const master = contract("deckmaster-contract.schema.json");
  const slideDraft = contract("slide-draft.schema.json");
  const api = contract("api-wire.json");

  assert.equal(master.$id, "urn:presenton:authored-vnext:deck-master-contract:v1");
  assert.equal(slideDraft.$id, "urn:presenton:authored-vnext:slide-draft:v2");
  assert.equal(master["x-pydantic-strict"], true);
  assert.equal(slideDraft["x-pydantic-strict"], true);
  assert.deepEqual(
    Object.keys(master.properties),
    [...MASTER_CONTRACT_KEY_MANIFEST.contract].sort(),
  );
  assert.deepEqual(
    Object.keys(slideDraft.properties),
    [...MASTER_CONTRACT_KEY_MANIFEST.slideDraft].sort(),
  );

  assert.equal(api.router.publicPrefix, AUTHORED_VNEXT_MASTER_API_PREFIX);
  assert.equal(api.featureFlag.default, false);
  assert.deepEqual(api.featureFlag.disabledResponse, {
    detail: "authored_vnext_master_disabled",
    status: 404,
  });
  assert.deepEqual(api.versions, {
    compilerPort: "OoxmlMasterCompilerPort",
    deckMasterContract: "deckmaster-contract/v1",
    slideSpec: "slidespec/v2",
  });

  const generatedPaths = new Set(
    api.endpoints.map(({ path: endpointPath }) =>
      `${api.router.mountPrefix}${endpointPath}`,
    ),
  );
  assert.ok(generatedPaths.has(AUTHORED_VNEXT_MASTER_ENDPOINTS.status));
  assert.ok(generatedPaths.has(AUTHORED_VNEXT_MASTER_ENDPOINTS.drafts));
  assert.ok(generatedPaths.has(AUTHORED_VNEXT_MASTER_ENDPOINTS.preview));
  assert.ok(generatedPaths.has(AUTHORED_VNEXT_MASTER_ENDPOINTS.decks));
  assert.ok(
    generatedPaths.has(
      `${AUTHORED_VNEXT_MASTER_API_PREFIX}/drafts/{master_id}/validate`,
    ),
  );
  assert.ok(
    generatedPaths.has(
      `${AUTHORED_VNEXT_MASTER_API_PREFIX}/drafts/{master_id}/publish`,
    ),
  );
  assert.ok(
    generatedPaths.has(
      `${AUTHORED_VNEXT_MASTER_API_PREFIX}/decks/{deck_id}/slides/{slide_id}/regenerate-body`,
    ),
  );
  assert.equal(
    AUTHORED_VNEXT_MASTER_ENDPOINTS.validate("master:test"),
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/drafts/master%3Atest/validate`,
  );
  assert.equal(
    AUTHORED_VNEXT_MASTER_ENDPOINTS.regenerateBody(
      "deck:test",
      "slide:test",
    ),
    `${AUTHORED_VNEXT_MASTER_API_PREFIX}/decks/deck%3Atest/slides/slide%3Atest/regenerate-body`,
  );
});
