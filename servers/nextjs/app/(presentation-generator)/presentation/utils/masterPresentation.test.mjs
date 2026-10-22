import assert from "node:assert/strict";
import test from "node:test";

import {
  presentationExportQualityAdvisory,
} from "./masterPresentation.ts";

const deck = (qualityReceiptV3) => ({ theme: { authoredQualityGate: { qualityReceiptV3 } } });

test("quality status remains a Korean advisory", () => {
  assert.match(
    presentationExportQualityAdvisory(deck({ gatePassed: false, blockers: ["visualReview.required_failed"] })) ?? "",
    /미실시/,
  );
  assert.match(
    presentationExportQualityAdvisory(deck({ gatePassed: false, blockers: ["visualReview.failed"] })) ?? "",
    /미통과/,
  );
  assert.match(
    presentationExportQualityAdvisory(deck({ gatePassed: false, blockers: ["capacity.required_failed:body"] })) ?? "",
    /확인 필요/,
  );
});

test("an absent receipt is also shown as unassessed advisory evidence", () => {
  assert.match(presentationExportQualityAdvisory({ theme: { authoredQualityGate: {} } }) ?? "", /확인 필요/);
});

test("a passed receipt has no advisory", () => {
  assert.equal(presentationExportQualityAdvisory(deck({ gatePassed: true, blockers: [] })), undefined);
});
