// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Infographic elements: progress bars and gauges.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

import { renderElementHtml } from "./template-v2-json-to-html.test-harness.mjs";

test("infographic: a655 progress bar is slim, centered, and label-free", async () => {
  const html = await renderElementHtml({
    "type": "infographic",
    "position": {
      "x": 4,
      "y": 6
    },
    "size": {
      "width": 200,
      "height": 48
    },
    "data": {
      "type": "progress_bar",
      "min_value": 0,
      "max_value": 200,
      "value": 50
    },
    "colors": [
      "#E5E7EB",
      "#2563EB"
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:4px;top:6px;width:200px;height:48px;display:flex;align-items:center;justify-content:center;overflow:hidden\"><div style=\"position:relative;width:100%;height:9px;border-radius:999px;background:#E5E7EB;overflow:hidden\"><div style=\"height:100%;width:25%;border-radius:inherit;background:#2563EB\"></div></div></div>",
  );
});

test("infographic: short progress bar hides the label", async () => {
  const html = await renderElementHtml({
    "type": "infographic",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 200,
      "height": 20
    },
    "data": {
      "type": "progress_bar",
      "value": 25
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:200px;height:20px;display:flex;align-items:center;justify-content:center;overflow:hidden\"><div style=\"position:relative;width:100%;height:6px;border-radius:999px;background:#E5E7EB;overflow:hidden\"><div style=\"height:100%;width:25%;border-radius:inherit;background:#7F22FE\"></div></div></div>",
  );
});

test("infographic: default kind and fallback size", async () => {
  const html = await renderElementHtml({
    "type": "infographic",
    "position": {
      "x": 0,
      "y": 0
    },
    "data": {
      "value": 10
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:180px;height:40px;display:flex;align-items:center;justify-content:center;overflow:hidden\"><div style=\"position:relative;width:100%;height:7px;border-radius:999px;background:#E5E7EB;overflow:hidden\"><div style=\"height:100%;width:10%;border-radius:inherit;background:#7F22FE\"></div></div></div>",
  );
});

test("infographic: gauge", async () => {
  const html = await renderElementHtml({
    "type": "infographic",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 160,
      "height": 96
    },
    "data": {
      "type": "gauge",
      "min_value": 0,
      "max_value": 100,
      "value": 75
    },
    "colors": [
      "#EEEEEE",
      "#DC2626"
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:160px;height:96px;overflow:hidden\"><svg width=\"100%\" height=\"100%\" viewBox=\"0 0 120 72\" preserveAspectRatio=\"xMidYMid meet\" style=\"display:block\"><path d=\"M 12 60 A 48 48 0 0 1 108 60\" fill=\"none\" stroke=\"#EEEEEE\" stroke-width=\"12\" stroke-linecap=\"round\"/><path d=\"M 12 60.00000000000001 A 48 48 0 0 1 93.94112549695427 26.058874503045715\" fill=\"none\" stroke=\"#DC2626\" stroke-width=\"12\" stroke-linecap=\"round\"/></svg></div>",
  );
});

test("infographic: gauge at zero omits the progress arc", async () => {
  const html = await renderElementHtml({
    "type": "infographic",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 160,
      "height": 96
    },
    "data": {
      "type": "gauge",
      "min_value": 10,
      "max_value": 10,
      "value": 10
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:160px;height:96px;overflow:hidden\"><svg width=\"100%\" height=\"100%\" viewBox=\"0 0 120 72\" preserveAspectRatio=\"xMidYMid meet\" style=\"display:block\"><path d=\"M 12 60 A 48 48 0 0 1 108 60\" fill=\"none\" stroke=\"#E5E7EB\" stroke-width=\"12\" stroke-linecap=\"round\"/></svg></div>",
  );
});

test("infographic: inverted min/max are reordered and clamped", async () => {
  const html = await renderElementHtml({
    "type": "infographic",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 200,
      "height": 40
    },
    "data": {
      "type": "progress_bar",
      "min_value": 100,
      "max_value": 0,
      "value": 250
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:200px;height:40px;display:flex;align-items:center;justify-content:center;overflow:hidden\"><div style=\"position:relative;width:100%;height:7px;border-radius:999px;background:#E5E7EB;overflow:hidden\"><div style=\"height:100%;width:100%;border-radius:inherit;background:#7F22FE\"></div></div></div>",
  );
});
