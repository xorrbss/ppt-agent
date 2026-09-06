import assert from "node:assert/strict";
import test from "node:test";

import {
  nativeImageDecorationEnabled,
  planNativeImageDecoration,
} from "./native-image-decoration.ts";

const NO_RADIUS = { topLeft: 0, topRight: 0, bottomRight: 0, bottomLeft: 0 };
const UNIFORM_RADIUS = { topLeft: 12, topRight: 12, bottomRight: 12, bottomLeft: 12 };
const ASYMMETRIC_RADIUS = { topLeft: 12, topRight: 0, bottomRight: 12, bottomLeft: 0 };

test("enabled by default and only disabled by an explicit \"0\"", () => {
  const previous = process.env.AUTHORED_HYBRID_NATIVE_IMAGE_DECORATION;
  try {
    delete process.env.AUTHORED_HYBRID_NATIVE_IMAGE_DECORATION;
    assert.equal(nativeImageDecorationEnabled(), true);
    process.env.AUTHORED_HYBRID_NATIVE_IMAGE_DECORATION = "0";
    assert.equal(nativeImageDecorationEnabled(), false);
    process.env.AUTHORED_HYBRID_NATIVE_IMAGE_DECORATION = "1";
    assert.equal(nativeImageDecorationEnabled(), true);
  } finally {
    if (previous === undefined) delete process.env.AUTHORED_HYBRID_NATIVE_IMAGE_DECORATION;
    else process.env.AUTHORED_HYBRID_NATIVE_IMAGE_DECORATION = previous;
  }
});

test("a uniform border-radius with no border/shadow stays a native crop", () => {
  const plan = planNativeImageDecoration({
    widthPx: 200,
    heightPx: 100,
    borderRadiusPx: UNIFORM_RADIUS,
    border: null,
    boxShadow: null,
  });
  assert.equal(plan.mode, "native-crop");
  assert.match(plan.pictureGeometryXml, /<a:prstGeom prst="roundRect">/);
  assert.match(plan.pictureGeometryXml, /<a:gd name="adj" fmla="val \d+"\/>/);
});

test("a plain border or a solid (unblurred) shadow gets a native backing rectangle", () => {
  const withBorder = planNativeImageDecoration({
    widthPx: 200,
    heightPx: 100,
    borderRadiusPx: NO_RADIUS,
    border: { widthPx: 2, color: { hex: "112233", alpha: 1 } },
    boxShadow: null,
  });
  assert.equal(withBorder.mode, "native-crop-with-backing");
  assert.equal(withBorder.backingShapes.length, 1);
  const borderShapeXml = withBorder.backingShapes[0](99);
  assert.match(borderShapeXml, /DevAX native image border/);
  assert.match(borderShapeXml, /<a:solidFill><a:srgbClr val="112233">/);

  const withSolidShadow = planNativeImageDecoration({
    widthPx: 200,
    heightPx: 100,
    borderRadiusPx: UNIFORM_RADIUS,
    border: null,
    boxShadow: {
      offsetXPx: 4,
      offsetYPx: 4,
      blurPx: 0,
      spreadPx: 0,
      color: { hex: "000000", alpha: 0.35 },
    },
  });
  assert.equal(withSolidShadow.mode, "native-crop-with-backing");
  assert.equal(withSolidShadow.backingShapes.length, 1);
  const shadowShapeXml = withSolidShadow.backingShapes[0](100);
  assert.match(shadowShapeXml, /DevAX native image shadow/);
  assert.match(shadowShapeXml, /<a:alpha val="35000"\/>/);

  const withBoth = planNativeImageDecoration({
    widthPx: 200,
    heightPx: 100,
    borderRadiusPx: NO_RADIUS,
    border: { widthPx: 1, color: { hex: "445566", alpha: 1 } },
    boxShadow: {
      offsetXPx: 2,
      offsetYPx: 2,
      blurPx: 0,
      spreadPx: 4,
      color: { hex: "223344", alpha: 0.5 },
    },
  });
  assert.equal(withBoth.mode, "native-crop-with-backing");
  // Back-to-front: shadow paints first, border paints in front of it.
  assert.equal(withBoth.backingShapes.length, 2);
  assert.match(withBoth.backingShapes[0](1), /DevAX native image shadow/);
  assert.match(withBoth.backingShapes[1](2), /DevAX native image border/);
});

test("a blurred shadow or asymmetric radius always falls back to raster", () => {
  const blurred = planNativeImageDecoration({
    widthPx: 200,
    heightPx: 100,
    borderRadiusPx: NO_RADIUS,
    border: null,
    boxShadow: {
      offsetXPx: 0,
      offsetYPx: 4,
      blurPx: 8,
      spreadPx: 0,
      color: { hex: "000000", alpha: 0.4 },
    },
  });
  assert.deepEqual(blurred, { mode: "raster-fallback", reason: "blurred-shadow" });

  const asymmetric = planNativeImageDecoration({
    widthPx: 200,
    heightPx: 100,
    borderRadiusPx: ASYMMETRIC_RADIUS,
    border: null,
    boxShadow: null,
  });
  assert.deepEqual(asymmetric, { mode: "raster-fallback", reason: "asymmetric-radius" });
});
