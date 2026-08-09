// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Container elements, overflow resolution and box styling.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

import { renderElementHtml } from "./template-v2-json-to-html.test-harness.mjs";

test("container: positioned child renders in absolute mode", async () => {
  const html = await renderElementHtml({
    "type": "container",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "padding": {
      "top": 4,
      "right": 6,
      "bottom": 8,
      "left": 10
    },
    "fill": {
      "color": "#F3F4F6"
    },
    "child": {
      "type": "text",
      "position": {
        "x": 5,
        "y": 5
      },
      "size": {
        "width": 50,
        "height": 20
      },
      "text": "child"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;background-color:#F3F4F6;padding:4px 6px 8px 10px;display:flex;align-items:flex-start;justify-content:flex-start;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:5px;top:5px;width:50px;height:20px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">child</span></span></div></div>",
  );
});

test("container: unpositioned child renders in flow mode", async () => {
  const html = await renderElementHtml({
    "type": "container",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "alignment": {
      "horizontal": "center",
      "vertical": "middle"
    },
    "child": {
      "type": "text",
      "size": {
        "width": 50,
        "height": 20
      },
      "text": "flow"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;align-items:center;justify-content:center;overflow:visible\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:50px;height:20px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">flow</span></span></div></div>",
  );
});

test("container: no child", async () => {
  const html = await renderElementHtml({
    "type": "container",
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
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;align-items:flex-start;justify-content:flex-start;overflow:visible\"></div>",
  );
});

test("container: explicit overflow hidden", async () => {
  const html = await renderElementHtml({
    "type": "container",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "overflow": "hidden",
    "child": {
      "type": "text",
      "size": {
        "width": 10,
        "height": 10
      },
      "text": "x"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;align-items:flex-start;justify-content:flex-start;overflow:hidden\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">x</span></span></div></div>",
  );
});

test("container: clip flag forces hidden overflow", async () => {
  const html = await renderElementHtml({
    "type": "container",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "clip": true,
    "child": {
      "type": "text",
      "size": {
        "width": 10,
        "height": 10
      },
      "text": "x"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;align-items:flex-start;justify-content:flex-start;overflow:hidden\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:relative;flex-shrink:0;width:10px;height:10px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">x</span></span></div></div>",
  );
});

test("container: overflowing positioned image child clips", async () => {
  const html = await renderElementHtml({
    "type": "container",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "child": {
      "type": "image",
      "position": {
        "x": -10,
        "y": 0
      },
      "size": {
        "width": 400,
        "height": 300
      },
      "data": "data:image/png;base64,AA=="
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;align-items:flex-start;justify-content:flex-start;overflow:hidden\"><img alt=\"\" src=\"data:image/png;base64,AA==\" style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:-10px;top:0px;width:400px;height:300px;display:block;max-width:none;max-height:none;object-fit:contain;\"></div>",
  );
});

test("container: contained positioned image child stays visible", async () => {
  const html = await renderElementHtml({
    "type": "container",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "child": {
      "type": "image",
      "position": {
        "x": 0,
        "y": 0
      },
      "size": {
        "width": 100,
        "height": 60
      },
      "data": "data:image/png;base64,AA=="
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;align-items:flex-start;justify-content:flex-start;overflow:visible\"><img alt=\"\" src=\"data:image/png;base64,AA==\" style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:100px;height:60px;display:block;max-width:none;max-height:none;object-fit:contain;\"></div>",
  );
});

test("container: positioned image child with clip-path clips", async () => {
  const html = await renderElementHtml({
    "type": "container",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "child": {
      "type": "image",
      "position": {
        "x": 0,
        "y": 0
      },
      "size": {
        "width": 10,
        "height": 10
      },
      "clip_path": "circle(50%)",
      "data": "data:image/png;base64,AA=="
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;padding:0px 0px 0px 0px;display:flex;align-items:flex-start;justify-content:flex-start;overflow:hidden\"><div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:10px;height:10px;clip-path:circle(50%);-webkit-clip-path:circle(50%);overflow:hidden;\"><img alt=\"\" src=\"data:image/png;base64,AA==\" style=\"display:block;max-width:none;max-height:none;height:100%;width:100%;object-fit:contain;\"></div></div>",
  );
});

test("container: full box styling", async () => {
  const html = await renderElementHtml({
    "type": "container",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "fill": {
      "color": "#FFFFFF",
      "opacity": 0.8
    },
    "stroke": {
      "color": "#111827",
      "width": 2
    },
    "border_radius": {
      "tl": 6
    },
    "shadow": {
      "offsetX": 0,
      "offsetY": 4,
      "blur": 8,
      "color": "#000000",
      "opacity": 0.2
    },
    "opacity": 0.9,
    "rotation": 5
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;transform:rotate(5deg);transform-origin:center;background-color:rgba(255,255,255,0.8);border:2px solid #111827;border-radius:6px 6px 6px 6px;box-shadow:0px 4px 8px rgba(0,0,0,0.2);opacity:0.9;padding:0px 0px 0px 0px;display:flex;align-items:flex-start;justify-content:flex-start;overflow:visible\"></div>",
  );
});

test("container: per-corner border radius", async () => {
  const html = await renderElementHtml({
    "type": "container",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "borderRadius": {
      "tl": 1,
      "tr": 2,
      "br": 3,
      "bl": 4
    },
    "stroke": {
      "width": 1
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;border:1px solid transparent;border-radius:1px 2px 3px 4px;padding:0px 0px 0px 0px;display:flex;align-items:flex-start;justify-content:flex-start;overflow:visible\"></div>",
  );
});
