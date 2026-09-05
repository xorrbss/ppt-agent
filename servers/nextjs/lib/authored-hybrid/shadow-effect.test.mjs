import assert from "node:assert/strict";
import test from "node:test";

import { fallbackElementForContractElement } from "./export-slide-quality.ts";
import {
  prepareNativeElements,
  serializePreparedNativeElement,
  serializePreparedNativeElementOverlay,
  serializePreparedNativeElementUnderlay,
} from "./native-plan.ts";
import { AUTHORED_HYBRID_SHADOW_BROWSER_SOURCE } from "./shadow-effect-browser.ts";
import {
  outerShadowDirection,
  outerShadowEffectXml,
  outerShadowIsSafe,
  outerShadowMappingIsSafe,
  outerShadowScalePercent,
  promotedElementEmitsOuterShadow,
  shapeHasOpaqueShadowCarrier,
} from "./shadow-effect.ts";

// The browser helpers are plain hoisted functions; evaluate the same text
// Chrome runs so the classifier and the assembler are tested on one source.
const browser = new Function(
  `${AUTHORED_HYBRID_SHADOW_BROWSER_SOURCE}
  return { parseBoxShadowLayers, selectOuterShadow, residualBoxShadowCss, mappedResidualBoxShadow, boxShadowUnmapped, boxShadowPlan };`
)();

const bounds = (x, y, width, height) => ({
  px: { x, y, width, height },
  inches: { x: x / 96, y: y / 96, width: width / 96, height: height / 96 },
});

const base = (id, rect) => ({
  id,
  domPath: "body > div:nth-of-type(1)",
  tagName: "div",
  sourceIndex: 0,
  zOrder: 0,
  cssZIndex: 0,
  bounds: rect,
  rotationDeg: 0,
  opacity: 1,
});

const softShadow = {
  offsetXPx: 0,
  offsetYPx: 4,
  blurPx: 12,
  spreadPx: 0,
  color: { hex: "000000", alpha: 0.25 },
};

const textStyle = {
  fontFamily: "Arial, sans-serif",
  fontFamilies: ["Arial", "sans-serif"],
  cjkFallbackFamilies: ["Malgun Gothic"],
  fontSizePt: 18,
  fontWeight: 400,
  bold: false,
  italic: false,
  underline: false,
  strike: false,
  color: { hex: "123456", alpha: 1 },
  letterSpacingPt: 0,
  lineHeight: { points: 24, multiple: 1.333, source: "computed" },
  horizontalAlignment: "left",
  verticalAlignment: "middle",
  direction: "ltr",
  wrapMode: "wrap",
};

test("browser parser maps a single outer box-shadow and leaves nothing on the backplate", () => {
  const css = "rgba(0, 0, 0, 0.25) 0px 4px 12px 0px";
  const layers = browser.parseBoxShadowLayers(css);
  assert.equal(layers.length, 1);
  assert.deepEqual(browser.selectOuterShadow(layers), { index: 0, shadow: softShadow });
  assert.equal(browser.boxShadowUnmapped(css), false);
  assert.equal(browser.mappedResidualBoxShadow(css), "none");
  assert.deepEqual(browser.boxShadowPlan(css), {
    outerShadow: softShadow,
    backplateShadowLayers: 0,
    shadowLayers: [],
  });
});

test("browser parser keeps inset, three-layer, and unparseable shadows on the raster path", () => {
  const inset = "rgba(0, 0, 0, 0.3) 0px 2px 4px 0px inset";
  assert.equal(browser.selectOuterShadow(browser.parseBoxShadowLayers(inset)), null);
  assert.equal(browser.boxShadowUnmapped(inset), true);
  assert.equal(browser.mappedResidualBoxShadow(inset), inset);
  assert.deepEqual(browser.boxShadowPlan(inset), {
    outerShadow: undefined,
    backplateShadowLayers: 1,
    shadowLayers: [],
  });

  const three = [
    "rgba(0, 0, 0, 0.1) 0px 1px 2px 0px",
    "rgba(0, 0, 0, 0.1) 0px 2px 4px 0px",
    "rgba(0, 0, 0, 0.1) 0px 4px 8px 0px",
  ].join(", ");
  assert.equal(browser.boxShadowUnmapped(three), true);
  assert.equal(browser.boxShadowPlan(three).backplateShadowLayers, 3);

  const wideGamut = "color(display-p3 0 0 0 / 0.2) 0px 4px 12px 0px";
  assert.equal(browser.parseBoxShadowLayers(wideGamut), null);
  assert.equal(browser.boxShadowUnmapped(wideGamut), true);
  assert.deepEqual(browser.boxShadowPlan(wideGamut), {
    outerShadow: undefined,
    backplateShadowLayers: 1,
    shadowLayers: [],
  });
  assert.deepEqual(browser.boxShadowPlan("none"), {
    outerShadow: undefined,
    backplateShadowLayers: 0,
    shadowLayers: [],
  });
});

test("two-layer shadows emit the strongest layer natively and keep the other on the backplate", () => {
  // Tailwind shadow-lg as Chrome serializes it.
  const css = "rgba(0, 0, 0, 0.1) 0px 10px 15px -3px, rgba(0, 0, 0, 0.1) 0px 4px 6px -4px";
  const layers = browser.parseBoxShadowLayers(css);
  const selected = browser.selectOuterShadow(layers);
  assert.equal(selected.index, 0);
  assert.deepEqual(selected.shadow, {
    offsetXPx: 0,
    offsetYPx: 10,
    blurPx: 15,
    spreadPx: -3,
    color: { hex: "000000", alpha: 0.1 },
  });
  assert.equal(browser.mappedResidualBoxShadow(css), "rgba(0, 0, 0, 0.1) 0px 4px 6px -4px");
  const plan = browser.boxShadowPlan(css);
  assert.equal(plan.backplateShadowLayers, 1);
  assert.deepEqual(plan.shadowLayers, []);

  // A zero-blur residual layer is still recorded as an exact solid layer.
  const solidResidual = "rgba(0, 0, 0, 0.5) 0px 8px 16px 0px, rgb(16, 42, 86) 2px 2px 0px 0px";
  const solidPlan = browser.boxShadowPlan(solidResidual);
  assert.equal(solidPlan.outerShadow.blurPx, 16);
  assert.deepEqual(solidPlan.shadowLayers, [
    { offsetXPx: 2, offsetYPx: 2, spreadPx: 0, color: { hex: "102A56", alpha: 1 } },
  ]);
});

test("outer shadow effect XML uses EMU lengths, screen-space direction, and alpha", () => {
  const xml = outerShadowEffectXml(softShadow, 1, { x: 0, y: 0, width: 400, height: 240 });
  assert.equal(
    xml,
    // rotWithShape="1" so the effect rotates with a tilted shape, matching CSS.
    '<a:effectLst><a:outerShdw blurRad="114300" dist="38100" dir="5400000" algn="ctr" rotWithShape="1"><a:srgbClr val="000000"><a:alpha val="25000"/></a:srgbClr></a:outerShdw></a:effectLst>'
  );
  // Element opacity multiplies into the effect alpha like every other paint.
  assert.match(outerShadowEffectXml(softShadow, 0.5), /<a:alpha val="12500"\/>/);
  // Negative spread is approximated by scaling the shadow about its centre.
  const tightened = outerShadowEffectXml({ ...softShadow, spreadPx: -2 }, 1, { x: 0, y: 0, width: 400, height: 240 });
  assert.match(tightened, /sx="99000" sy="98333" algn="ctr"/);
  // Without bounds the spread is ignored rather than guessed.
  assert.doesNotMatch(outerShadowEffectXml({ ...softShadow, spreadPx: -2 }, 1), /sx=/);

  assert.equal(outerShadowDirection(0, 0), 0);
  assert.equal(outerShadowDirection(3, 0), 0);
  assert.equal(outerShadowDirection(4, 4), 2_700_000);
  assert.equal(outerShadowDirection(0, -4), 16_200_000);
  assert.equal(outerShadowScalePercent(2, 0), 100_000);
  // A small residual extent still clamps to the 10% floor...
  assert.equal(outerShadowScalePercent(-48, 100), 10_000);
  // ...but a spread that consumes the whole extent (or inverts it) erases the
  // shadow in CSS, so it must not be represented as a visible 10% blob.
  assert.equal(outerShadowScalePercent(-50, 100), null);
  assert.equal(outerShadowScalePercent(-500, 100), null);

  assert.equal(outerShadowIsSafe(softShadow), true);
  assert.equal(outerShadowIsSafe({ ...softShadow, blurPx: Number.POSITIVE_INFINITY }), false);
  assert.equal(outerShadowIsSafe({ ...softShadow, blurPx: -1 }), false);
  assert.equal(outerShadowIsSafe({ ...softShadow, color: { hex: "00000", alpha: 1 } }), false);
  assert.equal(outerShadowIsSafe(null), false);
});

test("shapeHasOpaqueShadowCarrier accepts only an opaque fill or a gradient", () => {
  assert.equal(shapeHasOpaqueShadowCarrier({ fill: { hex: "FFFFFF", alpha: 1 } }), true);
  assert.equal(shapeHasOpaqueShadowCarrier({ fill: { hex: "FFFFFF", alpha: 0.999 } }), true);
  assert.equal(shapeHasOpaqueShadowCarrier({ fill: { hex: "FFFFFF", alpha: 0.9 } }), false);
  assert.equal(shapeHasOpaqueShadowCarrier({ fill: null, stroke: { hex: "000000", alpha: 1 } }), false);
  assert.equal(shapeHasOpaqueShadowCarrier({ fill: null }), false);
  assert.equal(
    shapeHasOpaqueShadowCarrier({ fill: null, gradient: { angleDeg: 0, stops: [] } }),
    true
  );
});

test("outerShadowMappingIsSafe combines the two shapeIsSafe gates into one predicate", () => {
  // No outerShadow: trivially safe regardless of the base object.
  assert.equal(outerShadowMappingIsSafe({ outerShadow: undefined }, true), true);
  assert.equal(outerShadowMappingIsSafe({ outerShadow: undefined }, false), true);
  // An unsafe (e.g. non-finite) shadow value is refused independent of the base object.
  const unsafeShadow = { ...softShadow, blurPx: Number.NaN };
  assert.equal(outerShadowMappingIsSafe({ outerShadow: unsafeShadow }, true), false);
  assert.equal(outerShadowMappingIsSafe({ outerShadow: unsafeShadow }, false), false);
  // A safe shadow with no base object at all (e.g. border-only) is not refused here.
  assert.equal(outerShadowMappingIsSafe({ outerShadow: softShadow, fill: null }, false), true);
  // A safe shadow with a base object requires that base object to be opaque.
  assert.equal(
    outerShadowMappingIsSafe({ outerShadow: softShadow, fill: { hex: "FFFFFF", alpha: 1 } }, true),
    true
  );
  assert.equal(
    outerShadowMappingIsSafe({ outerShadow: softShadow, fill: { hex: "FFFFFF", alpha: 0.5 } }, true),
    false
  );
  assert.equal(
    outerShadowMappingIsSafe({ outerShadow: softShadow, fill: null }, true),
    false
  );
});

test("a spread that collapses the extent skips the effect instead of drawing a clamped blob", () => {
  const collapsingSpread = { ...softShadow, spreadPx: -60 };
  // With bounds, the collapsing spread must suppress the whole effect...
  assert.equal(outerShadowEffectXml(collapsingSpread, 1, { x: 0, y: 0, width: 100, height: 400 }), "");
  // ...even though the same shadow renders fine on a larger axis alone.
  assert.notEqual(outerShadowEffectXml(collapsingSpread, 1, { x: 0, y: 0, width: 400, height: 400 }), "");
});

test("a native shape with a mapped shadow serializes one outerShdw on its base object only", async () => {
  const element = {
    ...base("card", bounds(100, 100, 400, 240)),
    classification: { mode: "raster", candidateKind: "shape", reasons: ["external-paint"] },
    shape: {
      shape: "round-rectangle",
      fill: { hex: "FFFFFF", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 9,
      borderLines: [
        { side: "top", color: { hex: "1F55D5", alpha: 1 }, widthPt: 1.5 },
      ],
      outerShadow: softShadow,
      backplateShadowLayers: 0,
    },
  };
  const prepared = await prepareNativeElements([element], { includeRasterShapes: true });
  assert.equal(prepared.length, 1);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.equal((xml.match(/<p:sp>/g) ?? []).length, 2);
  assert.equal((xml.match(/<a:effectLst>/g) ?? []).length, 1);
  assert.match(
    xml,
    /<a:solidFill><a:srgbClr val="FFFFFF"><\/a:srgbClr><\/a:solidFill><a:ln><a:noFill\/><\/a:ln><a:effectLst><a:outerShdw blurRad="114300" dist="38100" dir="5400000" algn="ctr" rotWithShape="1">/
  );
  assert.ok(promotedElementEmitsOuterShadow(element));

  const unsafe = { ...element, shape: { ...element.shape, outerShadow: { ...softShadow, blurPx: Number.NaN } } };
  assert.equal((await prepareNativeElements([unsafe], { includeRasterShapes: true })).length, 0);
  assert.equal(promotedElementEmitsOuterShadow(unsafe), false);
});

test("text in a shadowed container is native text over a native container with the effect", async () => {
  const element = {
    ...base("chip", bounds(124, 120, 336, 60)),
    classification: { mode: "raster", candidateKind: "text", reasons: ["external-paint", "decorated-text"] },
    text: {
      role: "caption",
      plainText: "Chip label",
      paragraphs: ["Chip label"],
      style: textStyle,
      runs: [],
      containerShape: {
        bounds: bounds(100, 100, 400, 100),
        shape: {
          shape: "round-rectangle",
          fill: { hex: "EEEEFF", alpha: 1 },
          stroke: null,
          strokeWidthPt: 0,
          radiusPt: 8,
          outerShadow: { ...softShadow, offsetYPx: 2, blurPx: 6 },
        },
      },
    },
  };
  const prepared = await prepareNativeElements([element], { includeRasterText: true });
  assert.equal(prepared.length, 1);
  assert.equal(prepared[0].kind, "text");
  const underlay = serializePreparedNativeElementUnderlay(prepared[0], 3);
  assert.match(underlay, /<a:effectLst><a:outerShdw blurRad="57150" dist="19050" dir="5400000" algn="ctr" rotWithShape="1">/);
  const overlay = serializePreparedNativeElementOverlay(prepared[0], 4);
  assert.match(overlay, /Chip label/);
  assert.doesNotMatch(overlay, /<a:effectLst>/);
  assert.equal((serializePreparedNativeElement(prepared[0], 5).match(/<a:effectLst>/g) ?? []).length, 1);
  assert.ok(promotedElementEmitsOuterShadow(element));
  assert.equal(
    promotedElementEmitsOuterShadow({
      ...element,
      text: { ...element.text, containerShape: undefined },
    }),
    false
  );
});

test("a shadowed shape can never reach native classification, so no shadow-backplate-only reason is produced", () => {
  // `hasExternalPaint` in browser-source.ts treats any non-"none" box-shadow
  // as external paint, which forces raster classification with an
  // "external-paint" reason. `classification.mode === "native"` therefore
  // never coexists with shadow paint in real captures; the reporting branch
  // that used to special-case it was dead code and has been removed. Only a
  // gradient background (which is not box-shadow) can survive native
  // classification and then get rejected later by `shapeIsSafe`.
  const nativeShape = (shape) => ({
    ...base("panel", bounds(0, 0, 100, 100)),
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: { shape: "rectangle", fill: { hex: "FFFFFF", alpha: 1 }, stroke: null, strokeWidthPt: 0, radiusPt: 0, ...shape },
  });
  assert.deepEqual(
    fallbackElementForContractElement(nativeShape({ outerShadow: softShadow })).reasons,
    ["native-preparation-or-layer-safety-rejected"]
  );
  assert.deepEqual(
    fallbackElementForContractElement(nativeShape({ backplateShadowLayers: 2 })).reasons,
    ["native-preparation-or-layer-safety-rejected"]
  );
  assert.deepEqual(
    fallbackElementForContractElement(nativeShape({ shadowLayers: [{ offsetXPx: 1, offsetYPx: 1, spreadPx: 0, color: { hex: "000000", alpha: 1 } }] })).reasons,
    ["native-preparation-or-layer-safety-rejected"]
  );
  assert.deepEqual(fallbackElementForContractElement(nativeShape({})).reasons, ["native-preparation-or-layer-safety-rejected"]);
  // The gradient branch is real: a gradient background does not set
  // `hasExternalPaint`, so it does reach native classification before
  // `shapeIsSafe` rejects it for the gradient itself.
  assert.deepEqual(
    fallbackElementForContractElement(nativeShape({ fill: null, gradient: { angleDeg: 0, stops: [] } })).reasons,
    ["gradient-backplate-only"]
  );
});

// --- Defects: a mapped shadow must never be claimed as "will be emitted
// natively" unless a real native carrier will actually paint it. Verifying
// both halves separately (does the plan claim suppression? does the XML
// contain the effect?) let them drift apart before; this helper enforces the
// single invariant "suppressed implies emitted" (and its converse) directly.
async function shadowInvariant(element, prepareOptions) {
  const prepared = await prepareNativeElements([element], prepareOptions);
  const xml = prepared
    .map((item) => serializePreparedNativeElement(item, 3))
    .join("");
  const claims = promotedElementEmitsOuterShadow(element);
  const emitted = /<a:outerShdw\b/.test(xml);
  assert.equal(
    claims,
    emitted,
    claims
      ? `promotedElementEmitsOuterShadow(${element.id}) claimed a shadow that no native output emits`
      : `promotedElementEmitsOuterShadow(${element.id}) missed a shadow that native output does emit`
  );
  return { prepared, xml, claims };
}

test("a transparent card with only a border has no native shadow carrier, so the backplate keeps the shadow", async () => {
  const element = {
    ...base("accent-card", bounds(100, 100, 400, 240)),
    classification: { mode: "raster", candidateKind: "shape", reasons: ["external-paint"] },
    shape: {
      shape: "round-rectangle",
      fill: null,
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 9,
      borderLines: [
        { side: "left", color: { hex: "1F55D5", alpha: 1 }, widthPt: 2 },
        { side: "top", color: { hex: "1F55D5", alpha: 1 }, widthPt: 1 },
      ],
      outerShadow: softShadow,
    },
  };
  const { prepared, xml, claims } = await shadowInvariant(element, { includeRasterShapes: true });
  // The asymmetric border is still its own carrier and stays promoted...
  assert.equal(prepared.length, 1);
  assert.equal((xml.match(/<p:sp>/g) ?? []).length, 2);
  // ...but nothing in the plan carries the shadow, so it must not be claimed.
  assert.doesNotMatch(xml, /<a:outerShdw/);
  assert.equal(claims, false);
});

test("a translucent fill refuses the whole shadow mapping, leaving the shape and its shadow to the backplate", async () => {
  const element = {
    ...base("glass-card", bounds(100, 100, 400, 240)),
    classification: { mode: "raster", candidateKind: "shape", reasons: ["external-paint"] },
    shape: {
      shape: "round-rectangle",
      fill: { hex: "FFFFFF", alpha: 0.6 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 9,
      outerShadow: softShadow,
    },
  };
  const { prepared, claims } = await shadowInvariant(element, { includeRasterShapes: true });
  // Translucent fill + shadow is refused entirely, not partially mapped:
  // DrawingML would let the native shadow show through the see-through card.
  assert.equal(prepared.length, 0);
  assert.equal(claims, false);
});

test("a stroke-only shape refuses the whole shadow mapping instead of shadowing the stroke path", async () => {
  const element = {
    ...base("outline-card", bounds(100, 100, 400, 240)),
    classification: { mode: "raster", candidateKind: "shape", reasons: ["external-paint"] },
    shape: {
      shape: "round-rectangle",
      fill: null,
      stroke: { hex: "1F55D5", alpha: 1 },
      strokeWidthPt: 1.5,
      radiusPt: 9,
      outerShadow: softShadow,
    },
  };
  const { prepared, claims } = await shadowInvariant(element, { includeRasterShapes: true });
  assert.equal(prepared.length, 0);
  assert.equal(claims, false);
});

test("a spread that collapses the shadow is not claimed even on an otherwise-opaque base object", async () => {
  const element = {
    ...base("thin-card", bounds(100, 100, 40, 240)),
    classification: { mode: "raster", candidateKind: "shape", reasons: ["external-paint"] },
    shape: {
      shape: "round-rectangle",
      fill: { hex: "FFFFFF", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 4,
      // spreadPx: -30 on a 40px-wide box collapses the horizontal extent to
      // nothing (40 - 60 <= 0), which must erase the shadow rather than
      // clamp it to a visible blob (see `outerShadowScalePercent`).
      outerShadow: { ...softShadow, spreadPx: -30 },
    },
  };
  const { prepared, xml, claims } = await shadowInvariant(element, { includeRasterShapes: true });
  // The base object (fill) still promotes normally...
  assert.equal(prepared.length, 1);
  assert.match(xml, /<a:solidFill>/);
  // ...just without the effect that would have been a visible blob.
  assert.doesNotMatch(xml, /<a:outerShdw/);
  assert.equal(claims, false);
});

test("a rotated card's shadow rotates with the shape, matching CSS", async () => {
  const element = {
    ...base("tilted-card", bounds(100, 100, 400, 240)),
    classification: { mode: "raster", candidateKind: "shape", reasons: ["external-paint"] },
    rotationDeg: 15,
    shape: {
      shape: "round-rectangle",
      fill: { hex: "FFFFFF", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 9,
      outerShadow: softShadow,
    },
  };
  const { xml, claims } = await shadowInvariant(element, { includeRasterShapes: true });
  assert.match(xml, /<a:outerShdw[^>]*rotWithShape="1"/);
  // A rotated transform is still applied to the shape itself, independent of
  // the effect flag; rotWithShape="1" is what keeps the shadow attached to it.
  assert.match(xml, /<a:xfrm rot="900000">/);
  assert.equal(claims, true);
});
