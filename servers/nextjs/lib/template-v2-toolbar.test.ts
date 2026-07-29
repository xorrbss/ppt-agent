import assert from "node:assert/strict";
import test from "node:test";

import {
  positionTemplateV2Toolbar,
  projectTemplateV2ToolbarBox,
  resolveTemplateV2ToolbarRoute,
} from "./template-v2-toolbar.ts";

test("routes no, multi, image, icon, text, and text-list selections", () => {
  assert.deepEqual(
    resolveTemplateV2ToolbarRoute({ selectionCount: 0, element: null }),
    { kind: "none" }
  );
  assert.deepEqual(
    resolveTemplateV2ToolbarRoute({
      selectionCount: 3,
      element: { type: "chart" },
    }),
    { kind: "multi", count: 3 }
  );
  assert.equal(
    resolveTemplateV2ToolbarRoute({
      selectionCount: 1,
      element: { type: "image" },
    }).kind,
    "image"
  );
  assert.equal(
    resolveTemplateV2ToolbarRoute({
      selectionCount: 1,
      element: { type: "image", is_icon: true },
    }).kind,
    "icon"
  );
  assert.equal(
    resolveTemplateV2ToolbarRoute({
      selectionCount: 1,
      element: { type: "text" },
    }).kind,
    "text"
  );
  assert.equal(
    resolveTemplateV2ToolbarRoute({
      selectionCount: 1,
      element: { type: "text-list" },
    }).kind,
    "text-list"
  );
});

test("fails closed for design variables and unsupported element types", () => {
  assert.equal(
    resolveTemplateV2ToolbarRoute({
      selectionCount: 1,
      element: {
        type: "text",
        design_variables: [{ name: "unsafe-until-slide-ui-boundary" }],
      },
    }).kind,
    "unsupported"
  );
  assert.deepEqual(
    resolveTemplateV2ToolbarRoute({
      selectionCount: 1,
      element: { type: "chart" },
    }),
    {
      kind: "unsupported",
      reason: "element-type",
      element: { type: "chart" },
    }
  );
  assert.equal(
    resolveTemplateV2ToolbarRoute({
      selectionCount: 1,
      element: { type: "text", design_variables: [] },
    }).kind,
    "text"
  );
});

test("projects an anchor through zoom, pan, and scroll", () => {
  assert.deepEqual(
    projectTemplateV2ToolbarBox(
      { x: 20, y: 30, width: 100, height: 50 },
      {
        left: 10,
        top: 15,
        scale: 2,
        panX: 5,
        panY: -5,
        scrollLeft: 7,
        scrollTop: 3,
      }
    ),
    { x: 48, y: 67, width: 200, height: 100 }
  );
});

test("positions above when possible and clamps horizontally", () => {
  assert.deepEqual(
    positionTemplateV2Toolbar({
      anchor: { x: 195, y: 80, width: 20, height: 10 },
      bounds: { left: 0, top: 0, right: 220, bottom: 160 },
      toolbarWidth: 100,
      toolbarHeight: 30,
    }),
    { left: 112, top: 42, placement: "above", visible: true }
  );
});

test("uses below near the top and returns a hidden safe fallback without anchor", () => {
  assert.deepEqual(
    positionTemplateV2Toolbar({
      anchor: { x: 40, y: 4, width: 20, height: 10 },
      bounds: { left: 0, top: 0, right: 200, bottom: 100 },
      toolbarWidth: 80,
      toolbarHeight: 30,
    }),
    { left: 10, top: 22, placement: "below", visible: true }
  );
  assert.deepEqual(
    positionTemplateV2Toolbar({
      anchor: null,
      bounds: { left: 10, top: 20, right: 200, bottom: 100 },
      toolbarWidth: 80,
      toolbarHeight: 30,
    }),
    { left: 18, top: 28, placement: "fallback", visible: false }
  );
});
