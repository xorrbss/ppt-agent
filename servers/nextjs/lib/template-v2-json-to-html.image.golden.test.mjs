// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Image elements, including the icon-mask, clip-path and crop branches.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

// Backend asset resolution depends on the configured FastAPI origin; pin it so
// the resolved image URLs below stay stable.
process.env.NEXT_PUBLIC_FAST_API = "http://127.0.0.1:8000";

import { renderElementHtml } from "./template-v2-json-to-html.test-harness.mjs";

test("image: plain data-url image with cover fit and focus", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "data": "data:image/png;base64,AA==",
    "fit": "cover",
    "focus_x": 25,
    "focus_y": 75
  });

  assert.equal(
    html,
    "<img alt=\"\" src=\"data:image/png;base64,AA==\" style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:block;max-width:none;max-height:none;object-fit:cover;object-position:25% 75%;\">",
  );
});

test("image: missing data renders nothing", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    }
  });

  assert.equal(
    html,
    "",
  );
});

test("image: icon mask branch uses colour and mask size", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "data": "data:image/svg+xml;base64,AA==",
    "color": "#2563EB",
    "isIcon": true,
    "fit": "fill"
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#2563EB;background:currentColor;-webkit-mask:url('data:image/svg+xml;base64,AA==') center/100% 100% no-repeat;mask:url('data:image/svg+xml;base64,AA==') center/100% 100% no-repeat;\"></div>",
  );
});

test("image: icon flag without colour renders a normal image", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "data": "data:image/svg+xml;base64,AA==",
    "is_icon": true
  });

  assert.equal(
    html,
    "<img alt=\"\" src=\"data:image/svg+xml;base64,AA==\" style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:block;max-width:none;max-height:none;object-fit:contain;\">",
  );
});

test("image: clip-path wraps the image in a clipping frame", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "data": "data:image/png;base64,AA==",
    "clip_path": "circle(40% at 50% 50%)"
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;clip-path:circle(40% at 50% 50%);-webkit-clip-path:circle(40% at 50% 50%);overflow:hidden;\"><img alt=\"\" src=\"data:image/png;base64,AA==\" style=\"display:block;max-width:none;max-height:none;height:100%;width:100%;object-fit:contain;\"></div>",
  );
});

test("image: unsupported clip-path value is dropped", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "data": "data:image/png;base64,AA==",
    "clip_path": "url(#mask)"
  });

  assert.equal(
    html,
    "<img alt=\"\" src=\"data:image/png;base64,AA==\" style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:block;max-width:none;max-height:none;object-fit:contain;\">",
  );
});

test("image: path() clip-path is normalized to single quotes", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "data": "data:image/png;base64,AA==",
    "clipPath": "path(\"M 0 0 L 10 10 Z\")"
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;clip-path:path('M 0 0 L 10 10 Z');-webkit-clip-path:path('M 0 0 L 10 10 Z');overflow:hidden;\"><img alt=\"\" src=\"data:image/png;base64,AA==\" style=\"display:block;max-width:none;max-height:none;height:100%;width:100%;object-fit:contain;\"></div>",
  );
});

test("image: crop scale adds a transform wrapper", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "data": "data:image/png;base64,AA==",
    "crop_scale": 2,
    "focus": [
      10,
      90
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;overflow:hidden;\"><img alt=\"\" src=\"data:image/png;base64,AA==\" style=\"display:block;max-width:none;max-height:none;height:100%;width:100%;object-fit:contain;object-position:10% 90%;transform:scale(2);transform-origin:10% 90%;\"></div>",
  );
});

test("image: crop scale is clamped to the supported range", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "data": "data:image/png;base64,AA==",
    "cropScale": 99
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;overflow:hidden;\"><img alt=\"\" src=\"data:image/png;base64,AA==\" style=\"display:block;max-width:none;max-height:none;height:100%;width:100%;object-fit:contain;transform:scale(6);transform-origin:center;\"></div>",
  );
});

test("image: backend app_data source is resolved", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "data": "/app_data/images/photo.png"
  });

  assert.equal(
    html,
    "<img alt=\"\" src=\"http://127.0.0.1:8000/app_data/images/photo.png\" style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:block;max-width:none;max-height:none;object-fit:contain;\">",
  );
});

test("image: relative non-backend source is left untouched", async () => {
  const html = await renderElementHtml({
    "type": "image",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "data": "photo.png"
  });

  assert.equal(
    html,
    "<img alt=\"\" src=\"photo.png\" style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:block;max-width:none;max-height:none;object-fit:contain;\">",
  );
});
