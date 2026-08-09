// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Table elements: header rows, spans, cell fonts and fills.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

import { renderElementHtml } from "./template-v2-json-to-html.test-harness.mjs";

test("table: no rows renders an empty frame", async () => {
  const html = await renderElementHtml({
    "type": "table",
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
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;overflow:hidden\"></div>",
  );
});

test("table: header row from columns plus body rows", async () => {
  const html = await renderElementHtml({
    "type": "table",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "columns": [
      {
        "text": "Name"
      },
      {
        "text": "Value"
      }
    ],
    "rows": [
      [
        {
          "text": "alpha"
        },
        {
          "text": "1"
        }
      ]
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr));overflow:hidden\"><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;font-weight:700;background:transparent;grid-column:1 / span 1;grid-row:1 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-weight:700;line-height:1.15;\">Name</span></div><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;font-weight:700;background:transparent;grid-column:2 / span 1;grid-row:1 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-weight:700;line-height:1.15;\">Value</span></div><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;background:transparent;grid-column:1 / span 1;grid-row:2 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;\">alpha</span></div><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;background:transparent;grid-column:2 / span 1;grid-row:2 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;\">1</span></div></div>",
  );
});

test("table: primitive cells reuse the table font", async () => {
  const html = await renderElementHtml({
    "type": "table",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "rows": [
      [
        "a",
        1
      ],
      [
        "b",
        2
      ]
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr));overflow:hidden\"><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;font-weight:700;background:transparent;grid-column:1 / span 1;grid-row:1 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:700;line-height:1.15;letter-spacing:0px;text-decoration:none;\"> </span></div><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;font-weight:700;background:transparent;grid-column:2 / span 1;grid-row:1 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:700;line-height:1.15;letter-spacing:0px;text-decoration:none;\"> </span></div><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;background:transparent;grid-column:1 / span 1;grid-row:2 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\"> </span></div><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;background:transparent;grid-column:2 / span 1;grid-row:2 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\"> </span></div></div>",
  );
});

test("table: explicit bold on a header cell suppresses the forced bold", async () => {
  const html = await renderElementHtml({
    "type": "table",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "columns": [
      {
        "text": "Head",
        "font": {
          "bold": false
        }
      }
    ],
    "rows": [
      [
        {
          "text": "Body"
        }
      ]
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:grid;grid-template-columns:repeat(1,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr));overflow:hidden\"><div style=\"color:#111827;font-family:'Arial';font-size:18px;font-weight:400;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;background:transparent;grid-column:1 / span 1;grid-row:1 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-weight:400;line-height:1.15;\">Head</span></div><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;background:transparent;grid-column:1 / span 1;grid-row:2 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;\">Body</span></div></div>",
  );
});

test("table: cell fill drives a readable text colour", async () => {
  const html = await renderElementHtml({
    "type": "table",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "rows": [
      [
        {
          "text": "dark",
          "color": {
            "color": "#111827"
          },
          "font": {
            "color": "#1F2937"
          }
        }
      ]
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:grid;grid-template-columns:repeat(1,minmax(0,1fr));grid-template-rows:repeat(1,minmax(0,1fr));overflow:hidden\"><div style=\"color:#1F2937;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;font-weight:700;background:#111827;grid-column:1 / span 1;grid-row:1 / span 1;\"><span style=\"color:#1F2937;font-family:'Arial';font-size:18px;font-weight:700;line-height:1.15;\">dark</span></div></div>",
  );
});

test("table: spans and covered cells", async () => {
  const html = await renderElementHtml({
    "type": "table",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "rows": [
      [
        {
          "text": "wide",
          "colspan": 2,
          "rowspan": 2
        },
        {
          "covered_by": {
            "row": 0,
            "column": 0
          }
        }
      ],
      [
        {
          "covered_by": {
            "row": 0,
            "column": 0
          }
        },
        {
          "text": "tail"
        }
      ]
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:repeat(2,minmax(0,1fr));overflow:hidden\"><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;font-weight:700;background:transparent;grid-column:1 / span 2;grid-row:1 / span 2;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-weight:700;line-height:1.15;\">wide</span></div><div style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;background:transparent;grid-column:2 / span 1;grid-row:2 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;line-height:1.15;\">tail</span></div></div>",
  );
});

test("table: cell runs, alignment and stroke overrides", async () => {
  const html = await renderElementHtml({
    "type": "table",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "font": {
      "family": "Georgia",
      "size": 12
    },
    "rows": [
      [
        {
          "alignment": "center",
          "stroke": {
            "color": "#EF4444",
            "width": 2,
            "opacity": 0.5
          },
          "runs": [
            {
              "text": "run",
              "font": {
                "italic": true
              }
            }
          ]
        }
      ]
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:grid;grid-template-columns:repeat(1,minmax(0,1fr));grid-template-rows:repeat(1,minmax(0,1fr));overflow:hidden\"><div style=\"color:#111827;font-family:'Georgia';font-size:12px;font-style:italic;line-height:1.15;display:flex;align-items:center;justify-content:center;border:2px solid rgba(239,68,68,0.5);min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:center;vertical-align:middle;white-space:pre-wrap;word-break:break-word;font-weight:700;background:transparent;grid-column:1 / span 1;grid-row:1 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:italic;font-weight:700;line-height:1.15;letter-spacing:0px;text-decoration:none;\">run</span></div></div>",
  );
});

test("table: nested text record cell", async () => {
  const html = await renderElementHtml({
    "type": "table",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "rows": [
      [
        {
          "text": {
            "text": "nested",
            "font": {
              "bold": true
            }
          }
        }
      ]
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;display:grid;grid-template-columns:repeat(1,minmax(0,1fr));grid-template-rows:repeat(1,minmax(0,1fr));overflow:hidden\"><div style=\"color:#111827;font-family:'Arial';font-size:18px;font-weight:700;line-height:1.15;display:flex;align-items:center;justify-content:flex-start;border:1px solid #D1D5DB;min-height:0;min-width:0;overflow:hidden;padding:4px 6px;text-align:left;vertical-align:middle;white-space:pre-wrap;word-break:break-word;background:transparent;grid-column:1 / span 1;grid-row:1 / span 1;\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:700;line-height:1.15;letter-spacing:0px;text-decoration:none;\">nested</span></div></div>",
  );
});
