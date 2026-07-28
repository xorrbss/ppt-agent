import assert from "node:assert/strict";
import test from "node:test";

import {
  expandAuthoredFontFamilyStack,
  expandPowerPointLayoutFontFamilyStack,
  resolveAuthoredFontFallback,
  resolvePowerPointTypeface,
} from "./font-policy.ts";

const expected = new Map([
  ["Noto Sans KR", "Malgun Gothic"],
  ["Pretendard", "Malgun Gothic"],
  ["Noto Serif KR", "Batang"],
  ["Inter", "Aptos"],
  ["Roboto", "Aptos"],
  ["DM Sans", "Aptos"],
  ["Source Serif 4", "Cambria"],
  ["IBM Plex Mono", "Consolas"],
]);

test("central font policy covers the supported authored families", () => {
  for (const [authored, fallback] of expected) {
    assert.equal(resolveAuthoredFontFallback(authored), fallback);
    assert.equal(resolvePowerPointTypeface([authored], "fallback"), fallback);
    assert.equal(
      resolveAuthoredFontFallback(`"${authored.toLowerCase()}"`),
      fallback
    );
  }
});

test("CSS stacks retain the collected face first and insert the PPT fallback", () => {
  assert.deepEqual(
    expandAuthoredFontFamilyStack(["Pretendard", "sans-serif"]),
    ["Pretendard", "Malgun Gothic", "sans-serif"]
  );
  assert.deepEqual(
    expandAuthoredFontFamilyStack([
      "Source Serif 4",
      "Cambria",
      "serif",
    ]),
    ["Source Serif 4", "Cambria", "serif"]
  );
});

test("editable layout stacks measure with the same mapped face serialized to PPTX", () => {
  for (const [authored, fallback] of expected) {
    const families = [authored, fallback, "sans-serif"];
    const expectedLayout = [
      fallback,
      authored,
      ...(fallback === "Aptos" ? [] : ["Aptos"]),
      "sans-serif",
    ];
    assert.deepEqual(
      expandPowerPointLayoutFontFamilyStack(families),
      expectedLayout,
      `${authored} should be measured with ${fallback} first`
    );
    assert.equal(
      expandPowerPointLayoutFontFamilyStack(families)[0],
      resolvePowerPointTypeface(families, "Aptos")
    );
  }
});

test("editable layout stacks preserve unknown families and do not mutate input", () => {
  const families = Object.freeze([
    '"Custom Corporate"',
    "'Noto Sans KR'",
    "Malgun Gothic",
    "sans-serif",
  ]);
  assert.deepEqual(expandPowerPointLayoutFontFamilyStack(families), [
    "Custom Corporate",
    "Malgun Gothic",
    "Noto Sans KR",
    "Aptos",
    "sans-serif",
  ]);
  assert.deepEqual(families, [
    '"Custom Corporate"',
    "'Noto Sans KR'",
    "Malgun Gothic",
    "sans-serif",
  ]);
});

test("editable layout stacks put Office-safe generic faces before CSS generics", () => {
  assert.deepEqual(
    expandPowerPointLayoutFontFamilyStack(["sans-serif"]),
    ["Aptos", "sans-serif"]
  );
  assert.deepEqual(
    expandPowerPointLayoutFontFamilyStack(["serif", "monospace"]),
    ["Cambria", "serif", "Consolas", "monospace"]
  );
});

test("unknown installed faces stay intact and generics use Office-safe defaults", () => {
  assert.equal(
    resolvePowerPointTypeface(["Custom Corporate", "sans-serif"], "Aptos"),
    "Custom Corporate"
  );
  assert.equal(resolvePowerPointTypeface(["sans-serif"], "fallback"), "Aptos");
  assert.equal(resolvePowerPointTypeface(["serif"], "fallback"), "Cambria");
  assert.equal(
    resolvePowerPointTypeface(["monospace"], "fallback"),
    "Consolas"
  );
});

test("only successfully embedded authored families bypass the central fallback", () => {
  const preserved = new Set(["noto sans kr"]);
  assert.equal(
    resolvePowerPointTypeface(
      ['"Noto Sans KR"', "Malgun Gothic", "sans-serif"],
      "Aptos",
      { preserveAuthoredFamilies: preserved }
    ),
    "Noto Sans KR"
  );
  assert.equal(
    resolvePowerPointTypeface(["Pretendard", "sans-serif"], "Aptos", {
      preserveAuthoredFamilies: preserved,
    }),
    "Malgun Gothic"
  );
  assert.equal(
    resolvePowerPointTypeface(["Noto Sans KR"], "Aptos"),
    "Malgun Gothic",
    "the default remains the existing non-embedded fallback policy"
  );
});
