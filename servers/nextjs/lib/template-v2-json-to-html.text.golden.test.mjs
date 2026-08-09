// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Text and math elements.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

import { renderElementHtml } from "./template-v2-json-to-html.test-harness.mjs";

test("text: bare text field with no runs and no font", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "text": "Hello"
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">Hello</span></span></div>",
  );
});

test("text: runs inherit the element font and override per run", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "font": {
      "family": "Inter",
      "size": 20,
      "color": "#111827",
      "bold": false,
      "italic": false,
      "lineHeight": 1.4,
      "letterSpacing": 0.5
    },
    "runs": [
      {
        "text": "Bold",
        "font": {
          "bold": true
        }
      },
      {
        "text": "Italic",
        "font": {
          "italic": true,
          "color": "#FF0000"
        }
      }
    ],
    "alignment": {
      "horizontal": "center",
      "vertical": "middle"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;font-family:'Inter';font-size:20px;font-style:normal;font-weight:400;letter-spacing:0.5px;display:flex;align-items:center;justify-content:center;line-height:1.4;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:center;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Inter';font-size:20px;font-style:normal;font-weight:700;line-height:1.4;letter-spacing:0.5px;text-decoration:none;\">Bold</span><span style=\"color:#FF0000;font-family:'Inter';font-size:20px;font-style:italic;font-weight:400;line-height:1.4;letter-spacing:0.5px;text-decoration:none;\">Italic</span></span></div>",
  );
});

test("text: underline true/false per run", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "font": {
      "family": "Arial",
      "size": 24,
      "color": "#111827",
      "underline": true
    },
    "runs": [
      {
        "text": "Under",
        "font": {
          "underline": true
        }
      },
      {
        "text": "Plain",
        "font": {
          "underline": false
        }
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;font-family:'Arial';font-size:24px;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:24px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:underline;\">Under</span><span style=\"color:#111827;font-family:'Arial';font-size:24px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">Plain</span></span></div>",
  );
});

test("text: legacy text_decoration underline field", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "font": {
      "family": "Arial",
      "size": 24,
      "color": "#111827"
    },
    "runs": [
      {
        "text": "Legacy",
        "font": {
          "text_decoration": "underline"
        }
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;font-family:'Arial';font-size:24px;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:24px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:underline;\">Legacy</span></span></div>",
  );
});

test("text: legacy textDecoration none field", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "runs": [
      {
        "text": "Legacy",
        "font": {
          "textDecoration": "none"
        }
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">Legacy</span></span></div>",
  );
});

test("text: right/bottom alignment", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "text": "Corner",
    "alignment": {
      "horizontal": "right",
      "vertical": "bottom"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;display:flex;align-items:flex-end;justify-content:flex-end;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:right;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">Corner</span></span></div>",
  );
});

test("text: unknown alignment values fall back to start", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "text": "Fallback",
    "alignment": {
      "horizontal": "justify",
      "vertical": "baseline"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">Fallback</span></span></div>",
  );
});

test("text: shadow, rotation and flips", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "text": "Shadowed",
    "rotation": 15,
    "flip_h": true,
    "flip_v": true,
    "shadow": {
      "offsetX": 2,
      "offsetY": 3,
      "blur": 4,
      "color": "#000000",
      "opacity": 0.5
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;transform:rotate(15deg) scaleX(-1) scaleY(-1);transform-origin:center;color:#111827;text-shadow:2px 3px 4px rgba(0,0,0,0.5);display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">Shadowed</span></span></div>",
  );
});

test("text: zero-opacity shadow emits no shadow", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "text": "No shadow",
    "shadow": {
      "offsetX": 2,
      "offsetY": 3,
      "blur": 4,
      "color": "#000000",
      "opacity": 0
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">No shadow</span></span></div>",
  );
});

test("text: html special characters are escaped", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "runs": [
      {
        "text": "<b>&\"'</b>"
      }
    ]
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">&lt;b&gt;&amp;&quot;&#39;&lt;/b&gt;</span></span></div>",
  );
});

test("text: unsafe font colour falls back to transparent", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "text": "Unsafe",
    "font": {
      "color": "url(javascript:alert(1))"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:transparent;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:transparent;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">Unsafe</span></span></div>",
  );
});

test("text: font colour opacity becomes rgba", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "text": "Faded",
    "font": {
      "color": "336699",
      "opacity": 0.4
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:rgba(51,102,153,0.4);display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:rgba(51,102,153,0.4);font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">Faded</span></span></div>",
  );
});

test("text: numeric font size given as string", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "text": "Stringy",
    "font": {
      "size": "18",
      "line_height": "2"
    }
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;font-size:18px;display:flex;align-items:flex-start;justify-content:flex-start;line-height:2;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">Stringy</span></span></div>",
  );
});

test("text: empty runs array falls back to the text field", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "runs": [],
    "text": ""
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\"> </span></span></div>",
  );
});

test("text: markdown in the raw text field is normalized into runs", async () => {
  const html = await renderElementHtml({
    "type": "text",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "text": "plain **bold** tail"
  });

  assert.equal(
    html,
    "<div style=\"box-sizing:border-box;min-height:0;min-width:0;position:absolute;left:12px;top:24px;width:240px;height:120px;color:#111827;display:flex;align-items:flex-start;justify-content:flex-start;line-height:1.1;overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;text-align:left;\"><span style=\"display:block;width:100%\"><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\">plain </span><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:700;line-height:1.15;letter-spacing:0px;text-decoration:none;\">bold</span><span style=\"color:#111827;font-family:'Arial';font-size:18px;font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;text-decoration:none;\"> tail</span></span></div>",
  );
});

test("math: empty latex renders nothing", async () => {
  const html = await renderElementHtml({
    "type": "math",
    "position": {
      "x": 12,
      "y": 24
    },
    "size": {
      "width": 240,
      "height": 120
    },
    "latex": "   "
  });

  assert.equal(
    html,
    "",
  );
});
