import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  isUnsupportedPresentationDriverError,
  isUnsupportedPresentationListItem,
} from "./unsupported-presentation.ts";

test("recognises the driver refusal in every ApiError shape", () => {
  assert.equal(
    isUnsupportedPresentationDriverError({ status: 422, code: "unsupported_presentation_driver" }),
    true,
  );
  assert.equal(
    isUnsupportedPresentationDriverError(new Error("unsupported_presentation_driver")),
    true,
  );
  assert.equal(
    isUnsupportedPresentationDriverError({ status: 422, detail: "unsupported_presentation_driver" }),
    true,
  );
  assert.equal(
    isUnsupportedPresentationDriverError({ status: 422, detail: { code: "unsupported_presentation_driver" } }),
    true,
  );
  assert.equal(
    isUnsupportedPresentationDriverError({ status: 422, error: { code: "unsupported_presentation_driver:authored" } }),
    true,
  );
});

test("does not treat other failures as the driver refusal", () => {
  assert.equal(isUnsupportedPresentationDriverError(null), false);
  assert.equal(isUnsupportedPresentationDriverError("unsupported_presentation_driver"), false);
  assert.equal(isUnsupportedPresentationDriverError({ status: 404, code: "not_found" }), false);
  assert.equal(isUnsupportedPresentationDriverError({ status: 422, code: "unsupported_presentation_profile_version" }), false);
  assert.equal(
    isUnsupportedPresentationDriverError({ status: 500, code: "unsupported_presentation_driver" }),
    false,
  );
});

test("list rows are unsupported when projected so or when no profile resolves", () => {
  assert.equal(isUnsupportedPresentationListItem({ unsupported_template_format: true }, true), true);
  assert.equal(isUnsupportedPresentationListItem({ id: "x" }, false), true);
  assert.equal(isUnsupportedPresentationListItem({ id: "x" }, true), false);
});

test("dashboard card, editor and public view wire the unsupported handling", () => {
  const read = (p: string) => readFileSync(p, "utf8");
  const card = read("app/(presentation-generator)/(dashboard)/dashboard/components/PresentationCard.tsx");
  assert.match(card, /isUnsupportedPresentationListItem/);
  assert.match(card, /isUnsupported \? \(/);
  assert.match(card, /!isUnsupported && \(\s*<SharePopover/);
  const hook = read("app/(presentation-generator)/presentation/hooks/usePresentationData.ts");
  assert.match(hook, /isUnsupportedPresentationDriverError\(error\)/);
  const page = read("app/(presentation-generator)/presentation/components/PresentationPage.tsx");
  assert.match(page, /if \(unsupportedDriver\) \{\s*return <PresentationUnsupportedNotice/);
  const pub = read("app/p/[token]/PublicPresentationView.tsx");
  assert.match(pub, /setStatus\("unsupported"\)/);
});
