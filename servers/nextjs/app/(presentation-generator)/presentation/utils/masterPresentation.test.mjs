import assert from "node:assert/strict";
import test from "node:test";

import {
  presentationExportBlockReason,
  presentationHybridQualityBlockReason,
} from "./masterPresentation.ts";

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

test("a review that never ran blocks Hybrid only, not the whole deck", () => {
  const pending = deck({ gatePassed: false, blockers: ["visualReview.required_failed"] });
  assert.equal(presentationExportBlockReason(pending), undefined);
  assert.match(presentationHybridQualityBlockReason(pending) ?? "", /vision-QA/);
});

test("a review that ran and failed blocks every format", () => {
  const failed = deck({ gatePassed: false, blockers: ["visualReview.failed"] });
  const all = presentationExportBlockReason(failed);
  assert.match(all ?? "", /내보낼 수 없습니다/);
  // The Hybrid reason repeats it so the Hybrid options are withheld too.
  assert.equal(presentationHybridQualityBlockReason(failed), all);
});

test("a passed or absent receipt blocks nothing", () => {
  assert.equal(presentationExportBlockReason(deck({ gatePassed: true, blockers: [] })), undefined);
  assert.equal(presentationExportBlockReason({ theme: {} }), undefined);
  assert.equal(presentationExportBlockReason(undefined), undefined);
});
