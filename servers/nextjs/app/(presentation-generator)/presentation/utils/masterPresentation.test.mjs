import assert from "node:assert/strict";
import test from "node:test";

import { presentationHybridQualityBlockReason } from "./masterPresentation.ts";

const deck = (qualityReceiptV3) => ({ theme: { authoredQualityGate: { qualityReceiptV3 } } });

test("a passed receipt withholds nothing", () => {
  assert.equal(
    presentationHybridQualityBlockReason(deck({ gatePassed: true, blockers: [] })),
    undefined,
  );
});

test("a deck with no receipt is left to the server's compatibility path", () => {
  for (const value of [undefined, null, {}, { theme: null }, { theme: {} }, { theme: { authoredQualityGate: {} } }]) {
    assert.equal(presentationHybridQualityBlockReason(value), undefined);
  }
});

test("a pending visual review names the vision-QA pass, not a generic failure", () => {
  // `export.pptx` waives exactly this blocker for Fidelity and refuses it for
  // Hybrid, so the button would fail after the click if this said nothing.
  const reason = presentationHybridQualityBlockReason(
    deck({ gatePassed: false, blockers: ["visualReview.required_failed"] }),
  );
  assert.match(reason ?? "", /vision-QA/);
  assert.match(reason ?? "", /Fidelity/);
});

test("a visual review that ran and failed blocks every format, Fidelity included", () => {
  // `allow_visual_review_pending` waives only `visualReview.required_failed`,
  // so this deck cannot export at all. Telling the user Fidelity still works
  // sends them into an export that fails in the timeline.
  const reason = presentationHybridQualityBlockReason(
    deck({ gatePassed: false, blockers: ["visualReview.failed"] }),
  );
  assert.match(reason ?? "", /통과하지 못해/);
  assert.doesNotMatch(reason ?? "", /vision-QA/);
  assert.doesNotMatch(reason ?? "", /Fidelity PPTX와 PDF는 그대로/);
});

test("any other blocker is reported verbatim rather than guessed at", () => {
  const reason = presentationHybridQualityBlockReason(
    deck({ gatePassed: false, blockers: ["capacity.required_failed:body", "tier.t1_required"] }),
  );
  assert.match(reason ?? "", /capacity\.required_failed:body/);
  assert.match(reason ?? "", /tier\.t1_required/);
});

test("a non-string blocker entry cannot smuggle itself into the message", () => {
  const reason = presentationHybridQualityBlockReason(
    deck({ gatePassed: false, blockers: [{ code: "x" }, 7] }),
  );
  assert.match(reason ?? "", /사유 미기재/);
});
