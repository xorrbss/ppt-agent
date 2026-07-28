import assert from "node:assert/strict";
import test from "node:test";

import {
  applyNativeTextBoundsTransform,
  nativeTextTransformCandidates,
  resolveAuthoredHybridTextProfile,
  selectNativeTextFidelity,
} from "./text-fidelity.ts";

const bounds = (x, y, width, height) => ({
  px: { x, y, width, height },
  inches: { x: x / 96, y: y / 96, width: width / 96, height: height / 96 },
});

const style = (overrides = {}) => ({
  fontFamily: "Noto Sans KR",
  fontFamilies: ["Noto Sans KR"],
  cjkFallbackFamilies: ["Noto Sans CJK KR"],
  fontSizePt: 16,
  fontWeight: 400,
  bold: false,
  italic: false,
  underline: false,
  strike: false,
  color: { hex: "111111", alpha: 1 },
  letterSpacingPt: 0,
  lineHeight: { points: 20, multiple: 1.25, source: "computed" },
  horizontalAlignment: "left",
  verticalAlignment: "top",
  direction: "ltr",
  wrapMode: "wrap",
  ...overrides,
});

const text = (overrides = {}) => {
  const rootStyle = overrides.style ?? style();
  const layout = overrides.layout ?? {
    boxBounds: bounds(100, 100, 300, 60),
    contentBounds: bounds(108, 106, 284, 48),
    paintedTextBounds: bounds(108, 108, 180, 20),
    paddingPx: { top: 6, right: 8, bottom: 6, left: 8 },
    borderPx: { top: 0, right: 0, bottom: 0, left: 0 },
    marginPx: { top: 0, right: 0, bottom: 0, left: 0 },
    rowGapPx: 0,
    columnGapPx: 0,
    display: "block",
    flexDirection: null,
    alignItems: "normal",
    justifyContent: "normal",
    textAlignSource: "self",
    widthMode: "fixed",
    lineCount: 1,
    singleLine: true,
    paragraphSpacingPx: { before: 0, after: 0 },
  };
  return {
    role: "body",
    plainText: "텍스트",
    paragraphs: ["텍스트"],
    style: rootStyle,
    runs: [{
      text: "텍스트",
      bounds: bounds(108, 108, 60, 20),
      fragments: [bounds(108, 108, 60, 20)],
      style: rootStyle,
    }],
    layout,
    ...overrides,
  };
};

test("semantic profiles use only captured text and computed layout facts", () => {
  assert.equal(resolveAuthoredHybridTextProfile(text({
    role: "title", style: style({ fontSizePt: 32 }),
  })), "display-title");
  assert.equal(resolveAuthoredHybridTextProfile(text({
    role: "title",
    style: style({ fontSizePt: 32 }),
    layout: { ...text().layout, lineCount: 2, singleLine: false },
  })), "multiline-title");
  assert.equal(resolveAuthoredHybridTextProfile(text()), "body");
  assert.equal(resolveAuthoredHybridTextProfile(text({
    style: style({ fontSizePt: 12, horizontalAlignment: "center" }),
  })), "centered-label");
  assert.equal(resolveAuthoredHybridTextProfile(text({
    role: "caption", style: style({ fontSizePt: 9 }),
  })), "compact-caption");
  assert.equal(resolveAuthoredHybridTextProfile(text({
    layout: {
      ...text().layout,
      display: "table-cell",
      paddingPx: { top: 4, right: 12, bottom: 8, left: 6 },
    },
  })), "table-cell");
  const regular = style();
  const bold = style({ fontWeight: 700, bold: true });
  assert.equal(resolveAuthoredHybridTextProfile(text({
    runs: [
      { text: "regular", bounds: bounds(0, 0, 40, 20), fragments: [], style: regular },
      { text: "bold", bounds: bounds(40, 0, 30, 20), fragments: [], style: bold },
    ],
  })), "mixed-weight");
});

test("candidate search is deterministic, bounded, and includes native baseline", () => {
  const first = nativeTextTransformCandidates("multiline-title");
  assert.deepEqual(first, nativeTextTransformCandidates("multiline-title"));
  assert.deepEqual(first[0], {
    insetPt: 0, lineSpacingPt: 0, widthScale: 0, verticalPt: 0,
  });
  assert.equal(new Set(first.map(JSON.stringify)).size, first.length);
  for (const candidate of first) {
    assert.ok(candidate.insetPt === 0 ||
      Math.abs(candidate.insetPt) >= 0.25 && Math.abs(candidate.insetPt) <= 0.75);
    assert.ok(candidate.lineSpacingPt === 0 ||
      Math.abs(candidate.lineSpacingPt) >= 0.25 && Math.abs(candidate.lineSpacingPt) <= 1);
    assert.ok(candidate.widthScale === 0 ||
      Math.abs(candidate.widthScale) >= 0.002 && Math.abs(candidate.widthScale) <= 0.008);
    assert.ok(candidate.verticalPt === 0 ||
      Math.abs(candidate.verticalPt) >= 0.25 && Math.abs(candidate.verticalPt) <= 0.5);
  }
});

test("profile search preserves native baselines without positive calibration evidence", () => {
  const body = selectNativeTextFidelity(text(), bounds(100, 100, 300, 60).px);
  assert.equal(body.profile, "body");
  assert.deepEqual(body.transform, {
    insetPt: 0, lineSpacingPt: 0, widthScale: 0, verticalPt: 0,
  });
  const caption = selectNativeTextFidelity(text({
    role: "caption",
    style: style({ fontSizePt: 9 }),
  }), bounds(100, 100, 300, 60).px);
  assert.equal(caption.profile, "compact-caption");
  assert.deepEqual(caption.transform, {
    insetPt: 0, lineSpacingPt: 0, widthScale: 0, verticalPt: 0,
  });
  const mixed = selectNativeTextFidelity(text({
    runs: [
      {
        text: "regular",
        bounds: bounds(100, 100, 60, 20),
        fragments: [],
        style: style(),
      },
      {
        text: "bold",
        bounds: bounds(160, 100, 40, 20),
        fragments: [],
        style: style({ fontWeight: 700, bold: true }),
      },
    ],
  }), bounds(100, 100, 300, 60).px);
  assert.equal(mixed.profile, "mixed-weight");
  assert.deepEqual(mixed.transform, {
    insetPt: 0, lineSpacingPt: 0, widthScale: 0, verticalPt: 0,
  });
  const title = selectNativeTextFidelity(text({
    role: "title",
    style: style({
      fontSizePt: 32,
      lineHeight: { points: 38, multiple: 1.1875, source: "computed" },
    }),
    layout: {
      ...text().layout,
      boxBounds: bounds(100, 100, 500, 100),
      contentBounds: bounds(100, 100, 500, 100),
      paintedTextBounds: bounds(100, 102, 430, 72),
      lineCount: 2,
      singleLine: false,
    },
  }), bounds(100, 100, 500, 100).px);
  assert.equal(title.profile, "multiline-title");
  assert.deepEqual(title.transform, {
    insetPt: 0, lineSpacingPt: 0, widthScale: 0, verticalPt: 0,
  });
});

test("width calibration grows from the authored alignment anchor", () => {
  const source = { x: 100, y: 50, width: 200, height: 40 };
  const transform = {
    insetPt: 0, lineSpacingPt: 0, widthScale: 0.008, verticalPt: -0.5,
  };
  assert.deepEqual(applyNativeTextBoundsTransform(source, "left", transform),
    { x: 100, y: 49.333333333333336, width: 201.6, height: 40 });
  assert.deepEqual(applyNativeTextBoundsTransform(source, "center", transform),
    { x: 99.2, y: 49.333333333333336, width: 201.6, height: 40 });
  assert.deepEqual(applyNativeTextBoundsTransform(source, "right", transform),
    { x: 98.4, y: 49.333333333333336, width: 201.6, height: 40 });
});
