// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Vector (polygon/polyline/ellipse) and raw svg elements.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

import { renderElementHtml } from "./template-v2-json-to-html.test-harness.mjs";

test("vector: closed polygon with fill, stroke and dashes", async () => {
  const html = await renderElementHtml({
    "type": "vector",
    "points": [
      {
        "x": 10,
        "y": 10
      },
      {
        "x": 90,
        "y": 20
      },
      {
        "x": 50,
        "y": 80
      }
    ],
    "closed": true,
    "fill": {
      "color": "#2563EB",
      "opacity": 0.5
    },
    "stroke": {
      "color": "#111827",
      "width": 3,
      "dash": [
        4,
        2
      ]
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:10px;top:10px;width:80px;height:70px;overflow:visible\"><svg width=\"100%\" height=\"100%\" viewBox=\"0 0 80 70\" preserveAspectRatio=\"none\" style=\"display:block;overflow:visible\"><polygon points=\"0,0 80,10 40,70\" fill=\"rgba(37,99,235,0.5)\" stroke=\"#111827\" stroke-width=\"3\" stroke-dasharray=\"4 2\"/></svg></div>",
  );
});

test("vector: open polyline defaults to a black stroke", async () => {
  const html = await renderElementHtml({
    "type": "vector",
    "points": [
      {
        "x": 0,
        "y": 0
      },
      {
        "x": 40,
        "y": 40
      }
    ],
    "closed": false
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:40px;height:40px;overflow:visible\"><svg width=\"100%\" height=\"100%\" viewBox=\"0 0 40 40\" preserveAspectRatio=\"none\" style=\"display:block;overflow:visible\"><polyline points=\"0,0 40,40\" fill=\"none\" stroke=\"#000000\" stroke-width=\"1\"/></svg></div>",
  );
});

test("vector: ellipse shape renders an svg ellipse", async () => {
  const html = await renderElementHtml({
    "type": "vector",
    "shape": "ellipse",
    "points": [
      {
        "x": 0,
        "y": 0
      },
      {
        "x": 100,
        "y": 60
      }
    ],
    "fill": {
      "color": "#F59E0B"
    },
    "stroke": {
      "color": "#7C2D12",
      "width": 2
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:100px;height:60px;overflow:visible\"><svg width=\"100%\" height=\"100%\" viewBox=\"0 0 100 60\" preserveAspectRatio=\"none\" style=\"display:block;overflow:visible\"><ellipse cx=\"50\" cy=\"30\" rx=\"50\" ry=\"30\" fill=\"#F59E0B\" stroke=\"#7C2D12\" stroke-width=\"2\"/></svg></div>",
  );
});

test("vector: corner radii round the polygon", async () => {
  const html = await renderElementHtml({
    "type": "vector",
    "points": [
      {
        "x": 0,
        "y": 0
      },
      {
        "x": 80,
        "y": 0
      },
      {
        "x": 80,
        "y": 60
      },
      {
        "x": 0,
        "y": 60
      }
    ],
    "corner_radii": [
      8,
      8,
      8,
      8
    ],
    "fill": {
      "color": "#10B981"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:80px;height:60px;overflow:visible\"><svg width=\"100%\" height=\"100%\" viewBox=\"0 0 80 60\" preserveAspectRatio=\"none\" style=\"display:block;overflow:visible\"><polygon points=\"0,8 0.125,6.125 0.5,4.5 1.125,3.125 2,2 3.125,1.125 4.5,0.5 6.125,0.125 8,0 72,0 73.875,0.125 75.5,0.5 76.875,1.125 78,2 78.875,3.125 79.5,4.5 79.875,6.125 80,8 80,52 79.875,53.875 79.5,55.5 78.875,56.875 78,58 76.875,58.875 75.5,59.5 73.875,59.875 72,60 8,60 6.125,59.875 4.5,59.5 3.125,58.875 2,58 1.125,56.875 0.5,55.5 0.125,53.875 0,52\" fill=\"#10B981\"/></svg></div>",
  );
});

test("vector: smooth curve resamples the outline", async () => {
  const html = await renderElementHtml({
    "type": "vector",
    "points": [
      {
        "x": 0,
        "y": 0
      },
      {
        "x": 40,
        "y": 30
      },
      {
        "x": 80,
        "y": 0
      }
    ],
    "closed": false,
    "curve": {
      "type": "smooth",
      "tension": 0.5,
      "segments": 2
    },
    "stroke": {
      "color": "#DB2777",
      "width": 2
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:80px;height:30px;overflow:visible\"><svg width=\"100%\" height=\"100%\" viewBox=\"0 0 80 30\" preserveAspectRatio=\"none\" style=\"display:block;overflow:visible\"><polyline points=\"0,0 18.75,15.9375 40,30 61.25,15.9375 80,0\" fill=\"none\" stroke=\"#DB2777\" stroke-width=\"2\"/></svg></div>",
  );
});

test("vector: invalid curve settings fall back to raw points", async () => {
  const html = await renderElementHtml({
    "type": "vector",
    "points": [
      {
        "x": 0,
        "y": 0
      },
      {
        "x": 40,
        "y": 30
      },
      {
        "x": 80,
        "y": 0
      }
    ],
    "closed": false,
    "curve": {
      "type": "smooth",
      "tension": 5
    },
    "stroke": {
      "color": "#DB2777",
      "width": 2
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:80px;height:30px;overflow:visible\"><svg width=\"100%\" height=\"100%\" viewBox=\"0 0 80 30\" preserveAspectRatio=\"none\" style=\"display:block;overflow:visible\"><polyline points=\"0,0 40,30 80,0\" fill=\"none\" stroke=\"#DB2777\" stroke-width=\"2\"/></svg></div>",
  );
});

test("vector: fewer than two points renders nothing", async () => {
  const html = await renderElementHtml({
    "type": "vector",
    "points": [
      {
        "x": 1,
        "y": 2
      }
    ]
  });

  assert.equal(
    html,
    "",
  );
});

test("vector: no fill and zero stroke width renders nothing", async () => {
  const html = await renderElementHtml({
    "type": "vector",
    "points": [
      {
        "x": 0,
        "y": 0
      },
      {
        "x": 10,
        "y": 10
      },
      {
        "x": 20,
        "y": 0
      }
    ],
    "closed": true,
    "stroke": {
      "width": 0
    }
  });

  assert.equal(
    html,
    "",
  );
});

test("vector: shadow and opacity become filter styles", async () => {
  const html = await renderElementHtml({
    "type": "vector",
    "points": [
      {
        "x": 0,
        "y": 0
      },
      {
        "x": 20,
        "y": 0
      },
      {
        "x": 20,
        "y": 20
      }
    ],
    "closed": true,
    "fill": {
      "color": "#000000"
    },
    "opacity": 0.25,
    "shadow": {
      "offset_x": 1,
      "offset_y": 2,
      "blur": 3,
      "color": "#123456"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:0px;top:0px;width:20px;height:20px;filter:drop-shadow(1px 2px 3px #123456);opacity:0.25;overflow:visible\"><svg width=\"100%\" height=\"100%\" viewBox=\"0 0 20 20\" preserveAspectRatio=\"none\" style=\"display:block;overflow:visible\"><polygon points=\"0,0 20,0 20,20\" fill=\"#000000\"/></svg></div>",
  );
});

test("svg: markup is passed through untouched", async () => {
  const html = await renderElementHtml({
    "type": "svg",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "svg": "<svg viewBox=\"0 0 10 10\"><rect width=\"10\" height=\"10\"/></svg>",
    "rotation": 45
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;transform:rotate(45deg);transform-origin:center;overflow:hidden\"><svg viewBox=\"0 0 10 10\"><rect width=\"10\" height=\"10\"/></svg></div>",
  );
});

test("svg: missing markup renders nothing", async () => {
  const html = await renderElementHtml({
    "type": "svg",
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
