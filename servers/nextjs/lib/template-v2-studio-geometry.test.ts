import assert from "node:assert/strict";
import test from "node:test";

import {
  currentTemplateV2Geometry,
  updateTemplateV2GeometryField,
} from "./template-v2-studio-geometry.ts";

test("currentTemplateV2Geometry uses the canvas normalization rules", () => {
  assert.deepEqual(
    currentTemplateV2Geometry({
      type: "text",
      position: { x: 10.126, y: 20.555 },
      size: { width: 100.444, height: 40.999 },
      rotation: -10,
    }),
    {
      x: 10.13,
      y: 20.56,
      width: 100.44,
      height: 41,
      rotation: 350,
    }
  );
});

test("updateTemplateV2GeometryField updates one field and preserves the rest", () => {
  const element = {
    type: "image",
    position: { x: 10, y: 20 },
    size: { width: 100, height: 40 },
    rotation: 15,
  };

  assert.deepEqual(updateTemplateV2GeometryField(element, "x", "25.125"), {
    x: 25.13,
    y: 20,
    width: 100,
    height: 40,
    rotation: 15,
  });
  assert.deepEqual(updateTemplateV2GeometryField(element, "width", "2"), {
    x: 10,
    y: 20,
    width: 8,
    height: 40,
    rotation: 15,
  });
  assert.deepEqual(updateTemplateV2GeometryField(element, "rotation", "370"), {
    x: 10,
    y: 20,
    width: 100,
    height: 40,
    rotation: 10,
  });
});

test("updateTemplateV2GeometryField rejects invalid or unsupported edits", () => {
  const group = {
    type: "group",
    position: { x: 10, y: 20 },
    size: { width: 100, height: 40 },
  };

  assert.deepEqual(updateTemplateV2GeometryField(group, "y", "-5"), {
    x: 10,
    y: -5,
  });
  assert.equal(updateTemplateV2GeometryField(group, "width", "200"), null);
  assert.equal(updateTemplateV2GeometryField(group, "rotation", "20"), null);
  assert.equal(updateTemplateV2GeometryField(group, "x", ""), null);
  assert.equal(updateTemplateV2GeometryField(group, "x", "not-a-number"), null);
  assert.equal(
    updateTemplateV2GeometryField({ type: "vector" }, "x", "10"),
    null
  );
});
