import assert from "node:assert/strict";
import test from "node:test";

import {
  baselineEntries,
  baselineFingerprints,
  collectHardcodedUiText,
  findNewHardcodedOccurrences,
  isSourceFileIncluded,
} from "./check-i18n-hardcoding.mjs";

function texts(source) {
  return collectHardcodedUiText(source).map(({ text }) => text);
}

test("detects JSX text, expression, attributes, templates, and toast calls", () => {
  const source = `
    const View = ({count}) => <>
      <p>Hello there</p>
      <p>{"Expression text"}</p>
      <input placeholder="Search decks" aria-label="Search presentations" />
      <span>{\`You can add \${count} slides\`}</span>
    </>;
    notify.error("Upload failed", "Please try again");
  `;
  assert.deepEqual(texts(source), [
    "Hello there",
    "Expression text",
    "Search decks",
    "Search presentations",
    "You can add {…} slides",
    "Upload failed",
    "Please try again",
  ]);
});

test("allows catalog calls, dynamic values, technical tokens, and excluded slide templates", () => {
  const source = `
    const View = ({message, t}) => <>
      <p>{t("common.loading")}</p>
      <p>{message}</p>
      <span>PresentOn</span><span>PDF / PPTX</span>
    </>;
  `;
  assert.deepEqual(texts(source), []);
  assert.equal(isSourceFileIncluded("app/presentation-templates/demo/Slide.tsx"), false);
  assert.equal(isSourceFileIncluded("components/RealView.tsx"), true);
});

test("ratchet fails only when a fingerprint exceeds its frozen count", () => {
  const original = collectHardcodedUiText("const A = () => <p>Legacy label</p>", "components/A.tsx");
  const unchanged = collectHardcodedUiText("const A = () => <p>Legacy label</p>", "components/A.tsx");
  const duplicated = collectHardcodedUiText(
    "const A = () => <><p>Legacy label</p><p>Legacy label</p></>",
    "components/A.tsx",
  );
  const baseline = baselineEntries(original);
  assert.equal(findNewHardcodedOccurrences(unchanged, baseline).length, 0);
  assert.equal(findNewHardcodedOccurrences(duplicated, baseline).length, 1);
  const compactBaseline = baselineFingerprints(original);
  assert.equal(findNewHardcodedOccurrences(unchanged, compactBaseline).length, 0);
  assert.equal(findNewHardcodedOccurrences(duplicated, compactBaseline).length, 1);
});
