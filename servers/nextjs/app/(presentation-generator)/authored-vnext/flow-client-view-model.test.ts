import assert from "node:assert/strict";
import test from "node:test";

import type { DeckMasterContractV1 } from "@/lib/authored-vnext/master/contracts";
import { masterAssetDigests } from "./export-asset-digests";

test("export pins WYSIWYG image digests alongside brand assets", () => {
  const contract = {
    brandAssets: {
      logo: { digest: "sha256:" + "a".repeat(64) },
      wordmark: null,
    },
    semanticElements: [
      {
        type: "image",
        content: { assetId: "asset:logo-copy", digest: "sha256:" + "a".repeat(64) },
      },
      {
        type: "image",
        content: { assetId: "asset:diagram", digest: "sha256:" + "b".repeat(64) },
      },
      {
        type: "text",
        content: { digest: "sha256:" + "c".repeat(64) },
      },
    ],
  } as unknown as DeckMasterContractV1;

  assert.deepEqual(masterAssetDigests(contract), [
    "sha256:" + "a".repeat(64),
    "sha256:" + "b".repeat(64),
  ]);
});
