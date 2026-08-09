// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Grid, group, component and unknown-type dispatch fallbacks.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

import { renderElementHtml } from "./template-v2-json-to-html.test-harness.mjs";

test("grid: default single column template", async () => {
  const html = await renderElementHtml({
    "type": "grid",
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
        "text": "a"
      },
      {
        "type": "text",
        "size": {
          "width": 10,
          "height": 10
        },
        "text": "b"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:grid;grid-template-columns:repeat(1,240px);align-items:stretch;justify-items:stretch;column-gap:0px;row-gap:0px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">a</span></span></div><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">b</span></span></div></div>",
  );
});

test("grid: explicit columns, rows and pixel templates", async () => {
  const html = await renderElementHtml({
    "type": "grid",
    "position": {
      "x": 0,
      "y": 0
    },
    "size": {
      "width": 200,
      "height": 100
    },
    "columns": 2,
    "rows": 2,
    "gap": 10,
    "alignItems": "center",
    "justify_items": "flex-end",
    "children": [
      {
        "type": "text",
        "size": {
          "width": 10,
          "height": 10
        },
        "text": "a"
      },
      {
        "type": "text",
        "size": {
          "width": 10,
          "height": 10
        },
        "text": "b"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:200px;height:100px;padding:0px 0px 0px 0px;display:grid;grid-template-columns:repeat(2,95px);grid-template-rows:repeat(2,45px);align-items:center;justify-items:flex-end;column-gap:10px;row-gap:10px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">a</span></span></div><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">b</span></span></div></div>",
  );
});

test("grid: declared rows smaller than rendered rows", async () => {
  const html = await renderElementHtml({
    "type": "grid",
    "position": {
      "x": 0,
      "y": 0
    },
    "columns": 1,
    "rows": 1,
    "children": [
      {
        "type": "text",
        "size": {
          "width": 10,
          "height": 10
        },
        "text": "a"
      },
      {
        "type": "text",
        "size": {
          "width": 10,
          "height": 10
        },
        "text": "b"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;padding:0px 0px 0px 0px;display:grid;grid-template-columns:repeat(1,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr));align-items:stretch;justify-items:stretch;column-gap:0px;row-gap:0px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">a</span></span></div><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">b</span></span></div></div>",
  );
});

test("grid-view alias renders as grid", async () => {
  const html = await renderElementHtml({
    "type": "grid-view",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "columns": 3,
    "children": [
      {
        "type": "text",
        "size": {
          "width": 10,
          "height": 10
        },
        "text": "g"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:grid;grid-template-columns:repeat(3,80px);align-items:stretch;justify-items:stretch;column-gap:0px;row-gap:0px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">g</span></span></div></div>",
  );
});

test("group: size falls back to the children bounds", async () => {
  const html = await renderElementHtml({
    "type": "group",
    "position": {
      "x": 5,
      "y": 5
    },
    "children": [
      {
        "type": "text",
        "position": {
          "x": 10,
          "y": 20
        },
        "size": {
          "width": 30,
          "height": 40
        },
        "text": "a"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:5px;top:5px;width:40px;height:60px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:10px;top:20px;width:30px;height:40px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">a</span></span></div></div>",
  );
});

test("group: no children", async () => {
  const html = await renderElementHtml({
    "type": "group",
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
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;overflow:visible\"></div>",
  );
});

test("component: untyped element with an elements array becomes a group", async () => {
  const html = await renderElementHtml({
    "id": "component-one",
    "position": {
      "x": 3,
      "y": 4
    },
    "elements": [
      {
        "type": "text",
        "position": {
          "x": 0,
          "y": 0
        },
        "size": {
          "width": 20,
          "height": 10
        },
        "text": "c"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:3px;top:4px;width:20px;height:10px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:20px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">c</span></span></div></div>",
  );
});

test("dispatch: unknown type with children renders as a group", async () => {
  const html = await renderElementHtml({
    "type": "mystery",
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
        "text": "m"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">m</span></span></div></div>",
  );
});

test("dispatch: unknown type with a child renders as a container", async () => {
  const html = await renderElementHtml({
    "type": "mystery",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "child": {
      "type": "text",
      "size": {
        "width": 10,
        "height": 10
      },
      "text": "m"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;align-items:flex-start;justify-content:flex-start;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">m</span></span></div></div>",
  );
});

test("dispatch: unknown type without children renders nothing", async () => {
  const html = await renderElementHtml({
    "type": "mystery",
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

test("dispatch: element without a type renders nothing", async () => {
  const html = await renderElementHtml({
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "text": "orphan"
  });

  assert.equal(
    html,
    "",
  );
});
