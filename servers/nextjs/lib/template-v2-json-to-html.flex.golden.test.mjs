// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Flex (and list-view) elements, including wrap expansion.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

import { renderElementHtml } from "./template-v2-json-to-html.test-harness.mjs";

test("flex: row direction with gaps and padding", async () => {
  const html = await renderElementHtml({
    "type": "flex",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "direction": "row",
    "gap": 8,
    "padding": {
      "top": 2,
      "right": 2,
      "bottom": 2,
      "left": 2
    },
    "alignItems": "center",
    "justifyContent": "flex-end",
    "children": [
      {
        "type": "text",
        "size": {
          "width": 40,
          "height": 20
        },
        "text": "a"
      },
      {
        "type": "text",
        "size": {
          "width": 40,
          "height": 20
        },
        "text": "b"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:2px 2px 2px 2px;display:flex;flex-direction:row;flex-wrap:nowrap;align-items:center;justify-content:flex-end;gap:8px;column-gap:8px;row-gap:8px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:40px;height:20px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">a</span></span></div><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:40px;height:20px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">b</span></span></div></div>",
  );
});

test("flex: column direction expands past the declared height", async () => {
  const html = await renderElementHtml({
    "type": "flex",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 100,
      "height": 10
    },
    "row_gap": 5,
    "children": [
      {
        "type": "text",
        "size": {
          "width": 40,
          "height": 20
        },
        "text": "a"
      },
      {
        "type": "text",
        "size": {
          "width": 40,
          "height": 20
        },
        "text": "b"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:100px;height:10px;height:45px;padding:0px 0px 0px 0px;display:flex;flex-direction:column;flex-wrap:nowrap;align-items:stretch;justify-content:flex-start;gap:0px;column-gap:0px;row-gap:5px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:40px;height:20px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">a</span></span></div><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:40px;height:20px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">b</span></span></div></div>",
  );
});

test("flex: wrapping row computes the cross size", async () => {
  const html = await renderElementHtml({
    "type": "flex",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 100,
      "height": 10
    },
    "direction": "row",
    "wrap": true,
    "gap": 10,
    "children": [
      {
        "type": "text",
        "size": {
          "width": 60,
          "height": 20
        },
        "text": "a"
      },
      {
        "type": "text",
        "size": {
          "width": 60,
          "height": 20
        },
        "text": "b"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:100px;height:10px;height:50px;padding:0px 0px 0px 0px;display:flex;flex-direction:row;flex-wrap:wrap;align-items:stretch;justify-content:flex-start;gap:10px;column-gap:10px;row-gap:10px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:60px;height:20px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">a</span></span></div><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:60px;height:20px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">b</span></span></div></div>",
  );
});

test("flex: children fall back to the elements array", async () => {
  const html = await renderElementHtml({
    "type": "flex",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "elements": [
      {
        "type": "text",
        "size": {
          "width": 10,
          "height": 10
        },
        "text": "e"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;flex-direction:column;flex-wrap:nowrap;align-items:stretch;justify-content:flex-start;gap:0px;column-gap:0px;row-gap:0px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">e</span></span></div></div>",
  );
});

test("flex: repeated item template via count", async () => {
  const html = await renderElementHtml({
    "type": "flex",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "count": 2,
    "item": {
      "type": "text",
      "size": {
        "width": 10,
        "height": 10
      },
      "text": "r"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;flex-direction:column;flex-wrap:nowrap;align-items:stretch;justify-content:flex-start;gap:0px;column-gap:0px;row-gap:0px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">r</span></span></div><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">r</span></span></div></div>",
  );
});

test("flex: no children", async () => {
  const html = await renderElementHtml({
    "type": "flex",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "gap": 4
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;flex-direction:column;flex-wrap:nowrap;align-items:stretch;justify-content:flex-start;gap:4px;column-gap:4px;row-gap:4px;overflow:visible\"></div>",
  );
});

test("list-view alias renders as flex", async () => {
  const html = await renderElementHtml({
    "type": "list-view",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "children": [
      {
        "type": "text",
        "size": {
          "width": 10,
          "height": 10
        },
        "text": "l"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;flex-direction:column;flex-wrap:nowrap;align-items:stretch;justify-content:flex-start;gap:0px;column-gap:0px;row-gap:0px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">l</span></span></div></div>",
  );
});
