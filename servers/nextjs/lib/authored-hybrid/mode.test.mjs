import assert from "node:assert/strict";
import test from "node:test";

import { resolvePptxMode, resolveRequestedPptxMode } from "./mode.ts";
import {
  EDITABLE_HYBRID_SHADOW_PROFILE_ID,
  isAuthoredPresentation,
  presentationEditableHybridProfileId,
} from "./presentation-mode.ts";

test("missing mode uses fast fidelity while explicit hybrid remains available", () => {
  assert.deepEqual(resolvePptxMode(undefined), { ok: true, value: "fidelity" });
  assert.deepEqual(resolvePptxMode("fidelity"), {
    ok: true,
    value: "fidelity",
  });
  assert.deepEqual(resolvePptxMode("hybrid"), { ok: true, value: "hybrid" });
  assert.deepEqual(resolvePptxMode(null), { ok: false });
  assert.deepEqual(resolvePptxMode("editable"), { ok: false });
});

test("PDF ignores PPTX-only options", () => {
  assert.deepEqual(resolveRequestedPptxMode("pdf", "invalid"), {
    ok: true,
    value: "fidelity",
  });
});

test("authored identity honors explicit mode and legacy sentinels", () => {
  assert.equal(isAuthoredPresentation({ mode: "authored" }), true);
  assert.equal(
    isAuthoredPresentation({ mode: "template", theme: { mode: "authored" } }),
    false
  );
  assert.equal(
    isAuthoredPresentation({ theme: { mode: "authored" }, slides: [] }),
    true
  );
  assert.equal(
    isAuthoredPresentation({
      slides: [{ content: { __authored__: true }, html_content: "<p>x</p>" }],
    }),
    true
  );
  assert.equal(
    isAuthoredPresentation({ slides: [{ layout_group: "authored" }] }),
    true
  );
  assert.equal(isAuthoredPresentation({ slides: [{ layout_group: "default" }] }), false);
});

test("bounded native profile comes only from a complete unblocked Scene projection", () => {
  const presentation = {
    mode: "authored",
    theme: {
      authored_vnext: {
        schemaVersion: "authored-editable-hybrid-scene-projection/v1",
        profileId: EDITABLE_HYBRID_SHADOW_PROFILE_ID,
        deckId: "deck:semantic-example",
        deckRevision: 1,
        sceneDigest: `sha256:${"a".repeat(64)}`,
        sourceDigest: `sha256:${"b".repeat(64)}`,
        status: "shadow",
        reasonCode: "editable_hybrid_mvp_completion_not_sealed",
        promotionReady: false,
        deploymentAuthorized: false,
        scene: {
          schemaVersion: "deck-scene-ir/v1",
          deckId: "deck:semantic-example",
          revision: 1,
          sceneDigest: `sha256:${"a".repeat(64)}`,
          sourceDigest: `sha256:${"b".repeat(64)}`,
          hybridProfileId: EDITABLE_HYBRID_SHADOW_PROFILE_ID,
          slides: [{
            elements: [{ elementId: "qid:title" }],
            hybridExport: {
              schemaVersion: "slide-hybrid-export-plan/v1",
              ownerDecisions: [{
                ownerId: "qid:title",
                decision: "nativeEditable",
              }],
            },
          }],
        },
      },
    },
  };

  assert.equal(
    presentationEditableHybridProfileId(presentation),
    EDITABLE_HYBRID_SHADOW_PROFILE_ID,
  );
  presentation.theme.authored_vnext.scene.slides[0].hybridExport
    .ownerDecisions[0].decision = "publicationBlocked";
  assert.equal(presentationEditableHybridProfileId(presentation), undefined);
});
