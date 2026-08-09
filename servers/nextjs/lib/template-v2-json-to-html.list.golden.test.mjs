// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Text-list elements.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

import { renderElementHtml } from "./template-v2-json-to-html.test-harness.mjs";

test("text-list: default marker renders an unordered list", async () => {
  const html = await renderElementHtml({
    "type": "text-list",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "items": [
      [
        "alpha"
      ],
      [
        "beta"
      ]
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><ul style=\"margin:0;padding-left:24px;\"><li style=\"overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\"> </span></li><li style=\"overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\"> </span></li></ul></div>",
  );
});

test("text-list: number marker renders an ordered list", async () => {
  const html = await renderElementHtml({
    "type": "text-list",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "marker": "number",
    "font": {
      "family": "Inter",
      "size": 16,
      "color": "#1F2937"
    },
    "items": [
      [
        {
          "text": "one"
        }
      ],
      [
        {
          "text": "two",
          "font": {
            "bold": true
          }
        }
      ]
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#1F2937;font-family:'Inter';font-size:16px;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><ol style=\"margin:0;padding-left:24px;\"><li style=\"overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><span style=\"color:#1F2937;font-family:'Inter';font-size:16px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">one</span></li><li style=\"overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><span style=\"color:#1F2937;font-family:'Inter';font-size:16px;font-style:normal;font-weight:700;line-height:1.15;letter-spacing:0px;text-decoration:none;\">two</span></li></ol></div>",
  );
});

test("text-list: none marker drops padding and markers", async () => {
  const html = await renderElementHtml({
    "type": "text-list",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "marker": "none",
    "items": [
      {
        "text": "solo"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><ul style=\"margin:0;padding-left:0px;list-style-type:none;\"><li style=\"overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">solo</span></li></ul></div>",
  );
});

test("text-list: empty items array", async () => {
  const html = await renderElementHtml({
    "type": "text-list",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "items": []
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><ul style=\"margin:0;padding-left:24px;\"></ul></div>",
  );
});

test("text-list: record items with runs and per-item font", async () => {
  const html = await renderElementHtml({
    "type": "text-list",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "font": {
      "size": 14
    },
    "items": [
      {
        "runs": [
          {
            "text": "run-a"
          },
          {
            "text": "run-b",
            "font": {
              "italic": true
            }
          }
        ]
      },
      {
        "text": "plain",
        "font": {
          "color": "#0F766E"
        }
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;font-size:14px;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><ul style=\"margin:0;padding-left:24px;\"><li style=\"overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><span style=\"color:#111827;font-family:'Arial';font-size:14px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">run-a</span><span style=\"color:#111827;font-family:'Arial';font-size:14px;font-style:italic;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">run-b</span></li><li style=\"overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><span style=\"color:#0F766E;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">plain</span></li></ul></div>",
  );
});

test("text-list: primitive items", async () => {
  const html = await renderElementHtml({
    "type": "text-list",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "items": [
      "raw",
      7,
      true
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><ul style=\"margin:0;padding-left:24px;\"><li style=\"overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">raw</span></li><li style=\"overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">7</span></li><li style=\"overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">true</span></li></ul></div>",
  );
});
