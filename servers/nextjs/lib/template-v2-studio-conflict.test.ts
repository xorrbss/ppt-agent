import assert from "node:assert/strict";
import test from "node:test";

import {
  createTemplateV2ConflictRecoveryBundle,
  serializeTemplateV2ConflictRecoveryBundle,
  templateV2ConflictRecoveryFilename,
} from "./template-v2-studio-conflict.ts";

const snapshot = {
  templateId: "template/alpha",
  expectedRevision: 4,
  currentRevision: 6,
  layouts: {
    layouts: [{ id: "layout-1", extension: { retained: true } }],
    upstream_extension: "retained",
  },
};

test("conflict recovery bundle retains the rejected upstream wire payload", () => {
  const capturedAt = new Date("2026-07-25T04:05:06.789Z");
  const bundle = createTemplateV2ConflictRecoveryBundle(snapshot, capturedAt);

  assert.deepEqual(bundle, {
    kind: "presenton.template-v2-studio-conflict",
    schema_version: 1,
    template_id: "template/alpha",
    expected_revision: 4,
    current_revision: 6,
    captured_at: "2026-07-25T04:05:06.789Z",
    layouts: snapshot.layouts,
  });
  assert.notEqual(bundle.layouts, snapshot.layouts);
  assert.match(
    serializeTemplateV2ConflictRecoveryBundle(snapshot, capturedAt),
    /"upstream_extension": "retained"\n/
  );
});

test("conflict recovery filename is portable and deterministic", () => {
  assert.equal(
    templateV2ConflictRecoveryFilename(
      "template/alpha",
      new Date("2026-07-25T04:05:06.789Z")
    ),
    "template-alpha-conflict-2026-07-25T04-05-06Z.json"
  );
});

test("conflict recovery rejects invalid identity and revisions", () => {
  assert.throws(
    () =>
      createTemplateV2ConflictRecoveryBundle({
        ...snapshot,
        templateId: " ",
      }),
    /templateId/
  );
  assert.throws(
    () =>
      createTemplateV2ConflictRecoveryBundle({
        ...snapshot,
        expectedRevision: 0,
      }),
    /expectedRevision/
  );
});
