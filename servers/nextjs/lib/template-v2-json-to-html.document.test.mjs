// Characterization (golden-output) coverage for lib/template-v2-json-to-html.ts.
// Exported surface: document shell, fragment shell, chart script injection,
// font asset tags and renderable-UI detection.
//
// These assertions pin the renderer's CURRENT output byte for byte, quirks
// included, so that refactoring the module (for example splitting it) can be
// proven behavior-preserving. A diff here means the rendered HTML changed:
// do not relax an expectation to make new output pass.
import assert from "node:assert/strict";
import test from "node:test";

// Backend asset resolution and the Chart.js script URL are environment driven;
// pin both so the goldens below stay stable.
process.env.NEXT_PUBLIC_FAST_API = "http://127.0.0.1:8000";
delete process.env.NEXT_PUBLIC_CHART_JS_URL;
delete process.env.CHART_JS_URL;

import {
  SLIDE_ROOT_OPEN_TAG,
  loadTemplateV2HtmlRenderer,
} from "./template-v2-json-to-html.test-harness.mjs";

const TINY_TEXT = {
  type: "text",
  position: { x: 0, y: 0 },
  size: { width: 10, height: 10 },
  text: "hi",
};

const TINY_TEXT_HTML =
  '<div style="box-sizing:border-box;min-height:0;min-width:0;position:absolute;' +
  "left:0px;top:0px;width:10px;height:10px;color:#111827;display:flex;" +
  "align-items:flex-start;justify-content:flex-start;line-height:1.1;" +
  "overflow:visible;white-space:pre-wrap;overflow-wrap:anywhere;" +
  'word-break:break-word;text-align:left;"><span style="display:block;width:100%">' +
  "<span style=\"color:#111827;font-family:'Arial';font-size:18px;" +
  "font-style:normal;font-weight:400;line-height:1.15;letter-spacing:0px;" +
  'text-decoration:none;">hi</span></span></div>';

const EMPTY_RESOURCE_CONTRACT =
  '<script type="application/json" data-presenton-template-v2-resource-contract="true">{"images":[],"fonts":[]}</script>';

function assertReadinessContract(html) {
  assert.match(html, /data-presenton-template-v2-readiness="true"/);
  assert.match(html, /templateV2ResourceError='true'/);
  assert.match(html, /required-resource-failed/);
  assert.match(html, /face\.load\(\)/);
  assert.match(html, /image\.naturalWidth>0/);
  assert.match(html, /templateV2RenderState='pending'/);
  assert.match(html, /chart-render-failed/);
  assert.match(html, /chart-render-timeout/);
}

const CHART_ELEMENT = {
  type: "chart",
  position: { x: 0, y: 0 },
  size: { width: 100, height: 100 },
  chart_type: "bar",
  categories: ["a"],
  series: [{ name: "s", values: [1] }],
};

function documentShell({ background = "#FFFFFF", width = 1280, height = 720 }) {
  return {
    head:
      "<!doctype html>\n<html><head><meta charset=\"utf-8\"><style>\n" +
      `html,body{margin:0;width:100%;height:100%;overflow:hidden;background:${background}}\n` +
      "body{font-family:Arial,Helvetica,sans-serif}\n" +
      "*,*::before,*::after{box-sizing:border-box}\n</style></head><body>",
    slideRoot:
      '<div class="relative overflow-hidden" data-template-v2-html-slide="true"' +
      ` style="box-sizing:border-box;position:relative;width:${width}px;` +
      `height:${height}px;overflow:hidden;background:${background};` +
      'font-family:Arial,Helvetica,sans-serif">',
  };
}

async function fontTags(fonts) {
  const { templateV2UiToHtmlFragment } = await loadTemplateV2HtmlRenderer();
  const fragment = templateV2UiToHtmlFragment(
    { elements: [TINY_TEXT] },
    { fonts },
  );
  return fragment.slice(0, fragment.indexOf('<div class="relative'));
}

test("module exports exactly the documented public surface", async () => {
  const renderer = await loadTemplateV2HtmlRenderer();

  assert.deepEqual(Object.keys(renderer).sort(), [
    "TEMPLATE_V2_HTML_HEIGHT",
    "TEMPLATE_V2_HTML_WIDTH",
    "hasTemplateV2RenderableUi",
    "templateV2UiToHtml",
    "templateV2UiToHtmlFragment",
  ]);
  assert.equal(renderer.TEMPLATE_V2_HTML_WIDTH, 1280);
  assert.equal(renderer.TEMPLATE_V2_HTML_HEIGHT, 720);
});

test("templateV2UiToHtml renders the default document shell", async () => {
  const { templateV2UiToHtml } = await loadTemplateV2HtmlRenderer();
  const shell = documentShell({});

  const html = templateV2UiToHtml({ elements: [TINY_TEXT] });
  assert.ok(
    html.startsWith(
      `${shell.head}${shell.slideRoot}${TINY_TEXT_HTML}</div>${EMPTY_RESOURCE_CONTRACT}`,
    ),
  );
  assert.ok(html.endsWith("</script></body></html>"));
  assertReadinessContract(html);
});

test("templateV2UiToHtml honours width/height options and a bare hex background", async () => {
  const { templateV2UiToHtml } = await loadTemplateV2HtmlRenderer();
  const shell = documentShell({
    background: "#0f172a",
    width: 640,
    height: 360,
  });

  const html = templateV2UiToHtml(
    { elements: [TINY_TEXT], background: "0f172a" },
    { width: 640, height: 360 },
  );
  assert.ok(
    html.startsWith(
      `${shell.head}${shell.slideRoot}${TINY_TEXT_HTML}</div>${EMPTY_RESOURCE_CONTRACT}`,
    ),
  );
  assertReadinessContract(html);
});

test("templateV2UiToHtml replaces an unsafe background with transparent", async () => {
  const { templateV2UiToHtml } = await loadTemplateV2HtmlRenderer();
  const html = templateV2UiToHtml({
    elements: [TINY_TEXT],
    background: "red;}<script>",
  });

  assert.match(html, /background:transparent\}/);
  assert.match(html, /background:transparent;font-family:Arial/);
  assert.doesNotMatch(html, /<script>/);
});

test("raw optional flags and placeholder-looking basenames cannot weaken the resource gate", async () => {
  const { templateV2UiToHtml } = await loadTemplateV2HtmlRenderer();
  const image = (data, extra = {}) => ({
    type: "image",
    position: { x: 0, y: 0 },
    size: { width: 10, height: 10 },
    data,
    ...extra,
  });
  const html = templateV2UiToHtml(
    {
      elements: [
        image("/required.png", { decorative: true }),
        image("/explicit.png", { resource_policy: "optional" }),
        image("/static/icons/placeholder.svg"),
      ],
    },
    {
      fonts: {
        Required: "/required.ttf",
        Optional: { url: "/optional.ttf", resourcePolicy: "optional" },
      },
    },
  );
  const encoded = html.match(
    /data-presenton-template-v2-resource-contract="true">([^<]+)<\/script>/,
  )?.[1];
  assert.ok(encoded);
  assert.deepEqual(JSON.parse(encoded), {
    images: [
      { source: "/required.png", policy: "required" },
      { source: "/explicit.png", policy: "required" },
      {
        source: "http://127.0.0.1:8000/static/icons/placeholder.svg",
        policy: "required",
      },
    ],
    fonts: [
      { family: "Required", source: "/required.ttf", policy: "required" },
      { family: "Optional", source: "/optional.ttf", policy: "required" },
    ],
  });
});

test("templateV2UiToHtml returns null when there is nothing to render", async () => {
  const { templateV2UiToHtml, templateV2UiToHtmlFragment } =
    await loadTemplateV2HtmlRenderer();

  assert.equal(templateV2UiToHtml({}), null);
  assert.equal(templateV2UiToHtml(null), null);
  assert.equal(templateV2UiToHtml(undefined), null);
  assert.equal(templateV2UiToHtml("nope"), null);
  assert.equal(templateV2UiToHtml({ elements: [], components: [] }), null);
  assert.equal(templateV2UiToHtmlFragment({}), null);
  assert.equal(templateV2UiToHtmlFragment({ elements: [] }), null);
});

test("root elements render before components", async () => {
  const { templateV2UiToHtmlFragment } = await loadTemplateV2HtmlRenderer();
  const fragment = templateV2UiToHtmlFragment({
    elements: [{ ...TINY_TEXT, text: "E" }],
    components: [
      {
        id: "c",
        position: { x: 1, y: 2 },
        elements: [{ ...TINY_TEXT, text: "C" }],
      },
    ],
  });

  assert.equal(
    fragment,
    SLIDE_ROOT_OPEN_TAG +
      TINY_TEXT_HTML.replace(">hi<", ">E<") +
      '<div style="box-sizing:border-box;min-height:0;min-width:0;position:absolute;' +
      'left:1px;top:2px;width:10px;height:10px;overflow:visible">' +
      TINY_TEXT_HTML.replace(">hi<", ">C<") +
      "</div></div>",
  );
});

test("templateV2UiToHtmlFragment omits the document shell", async () => {
  const { templateV2UiToHtmlFragment } = await loadTemplateV2HtmlRenderer();
  const fragment = templateV2UiToHtmlFragment({ elements: [TINY_TEXT] });

  assert.equal(fragment, `${SLIDE_ROOT_OPEN_TAG}${TINY_TEXT_HTML}</div>`);
  assert.doesNotMatch(fragment, /<!doctype/i);
});

test("chart scripts are appended to the document when a chart is present", async () => {
  const { templateV2UiToHtml } = await loadTemplateV2HtmlRenderer();
  const html = templateV2UiToHtml({ elements: [CHART_ELEMENT] });
  const scripts = html.slice(html.indexOf("</div><script"));

  assert.ok(
    scripts.startsWith(
      '</div><script src="/vendor/chart-4.5.1.umd.min.js"></script>' +
        '<script src="/vendor/chartjs-plugin-datalabels-2.2.0.min.js"></script>' +
        "<script>\n(function(){\n",
    ),
    scripts.slice(0, 240),
  );
  assert.ok(scripts.endsWith("</script></body></html>"));
  assert.doesNotMatch(html, /cdn\.jsdelivr\.net/);
});

test("the inline chart bootstrap script is syntactically valid and self-escaping", async () => {
  const { templateV2UiToHtml } = await loadTemplateV2HtmlRenderer();
  const html = templateV2UiToHtml({ elements: [CHART_ELEMENT] });
  const inlineScripts = Array.from(
    html.matchAll(/<script>([\s\S]*?)<\/script>/g),
    (match) => match[1],
  );

  assert.equal(inlineScripts.length, 1);
  assert.doesNotThrow(() => new Function(inlineScripts[0]));
  assert.doesNotMatch(inlineScripts[0], /<\/script/);
  assert.match(inlineScripts[0], /Chart\.register\(window\.ChartDataLabels\)/);
  assert.match(inlineScripts[0], /templateV2ChartState="pending"/);
  assert.match(inlineScripts[0], /template-v2-chart-state/);
});

test("chart detection walks nested children, containers, item templates and components", async () => {
  const { templateV2UiToHtml } = await loadTemplateV2HtmlRenderer();
  const hasScripts = (ui) => templateV2UiToHtml(ui).includes("<script src=");

  assert.equal(
    hasScripts({
      elements: [
        { type: "group", position: { x: 0, y: 0 }, children: [CHART_ELEMENT] },
      ],
    }),
    true,
  );
  assert.equal(
    hasScripts({
      elements: [
        { type: "container", position: { x: 0, y: 0 }, child: CHART_ELEMENT },
      ],
    }),
    true,
  );
  assert.equal(
    hasScripts({
      elements: [
        {
          type: "flex",
          position: { x: 0, y: 0 },
          item: CHART_ELEMENT,
          count: 1,
        },
      ],
    }),
    true,
  );
  assert.equal(
    hasScripts({ elements: [{ id: "c", elements: [CHART_ELEMENT] }] }),
    true,
  );
  assert.equal(hasScripts({ elements: [TINY_TEXT] }), false);
});

test("fragments never carry the chart scripts, even with a chart element", async () => {
  const { templateV2UiToHtmlFragment } = await loadTemplateV2HtmlRenderer();
  const fragment = templateV2UiToHtmlFragment({ elements: [CHART_ELEMENT] });

  assert.match(fragment, /data-presenton-chart="true"/);
  assert.doesNotMatch(fragment, /<script/);
});

test("hasTemplateV2RenderableUi only accepts records with elements or components", async () => {
  const { hasTemplateV2RenderableUi } = await loadTemplateV2HtmlRenderer();

  assert.equal(hasTemplateV2RenderableUi({ elements: [1] }), true);
  assert.equal(hasTemplateV2RenderableUi({ components: [1] }), true);
  assert.equal(
    hasTemplateV2RenderableUi({ elements: [], components: [] }),
    false,
  );
  assert.equal(hasTemplateV2RenderableUi({}), false);
  assert.equal(hasTemplateV2RenderableUi(null), false);
  assert.equal(hasTemplateV2RenderableUi(undefined), false);
  assert.equal(hasTemplateV2RenderableUi("nope"), false);
  assert.equal(hasTemplateV2RenderableUi([{ elements: [1] }]), false);
});

const FONT_CASES = [
  {
    name: "a raw css string is inlined and aliased by family suffix",
    fonts:
      "@font-face{font-family:'Roboto Bold';src:url(/a.woff2);font-weight:700}",
    expected:
      "<style>@font-face{font-family:'Roboto Bold';src:url(/a.woff2);font-weight:700}" +
      "@font-face{font-family:'Roboto';src:url(/a.woff2);font-weight:700}</style>",
  },
  {
    name: "a css key on the fonts record is inlined",
    fonts: { css: "body{color:red}" },
    expected: "<style>body{color:red}</style>",
  },
  {
    name: "font_css closes style tags but leaves script tags alone",
    fonts: { font_css: "body{color:blue}</style><script>x</script>" },
    expected: "<style>body{color:blue}<\\/style><script>x</script></style>",
  },
  {
    name: "a family to url map becomes a @font-face rule with a resolved url",
    fonts: { Roboto: "/app_data/fonts/roboto.woff2" },
    expected:
      "<style>@font-face{font-family:'Roboto';" +
      'src:url("http://127.0.0.1:8000/app_data/fonts/roboto.woff2");' +
      "font-weight:400;font-style:normal;font-display:swap}</style>",
  },
  {
    name: "an italic family name infers font-style italic",
    fonts: { "Lato Italic": "/static/fonts/lato-italic.woff" },
    expected:
      "<style>@font-face{font-family:'Lato Italic';" +
      'src:url("http://127.0.0.1:8000/static/fonts/lato-italic.woff");' +
      "font-weight:400;font-style:italic;font-display:swap}</style>",
  },
  {
    name: "an array of face records keeps format, weight and style",
    fonts: [
      {
        family: "Merriweather",
        url: "https://cdn.example.com/m.woff2",
        format: "woff2",
        weight: "semi bold",
        style: "italic",
      },
    ],
    expected:
      "<style>@font-face{font-family:'Merriweather';" +
      'src:url("https://cdn.example.com/m.woff2") format("woff2");' +
      "font-weight:600;font-style:italic;font-display:swap}</style>",
  },
  {
    name: "a nested fonts key wins over the outer record",
    fonts: {
      fonts: [{ name: "Nested", src: "/nested.ttf" }],
      css: "h1{color:green}",
    },
    expected:
      "<style>h1{color:green}</style><style>@font-face{font-family:'Nested';" +
      'src:url("/nested.ttf");font-weight:400;font-style:normal;' +
      "font-display:swap}</style>",
  },
  {
    name: "a google fonts url becomes a stylesheet link",
    fonts: { Inter: "https://fonts.googleapis.com/css2?family=Inter" },
    expected:
      '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter">',
  },
  {
    name: "a .css url becomes a resolved stylesheet link",
    fonts: ["/app_data/fonts/pack.css"],
    expected:
      '<link rel="stylesheet" href="http://127.0.0.1:8000/app_data/fonts/pack.css">',
  },
  {
    name: "a numeric weight is rounded to a string",
    fonts: [{ family: "Numeric", url: "/n.woff", weight: 350 }],
    expected:
      "<style>@font-face{font-family:'Numeric';src:url(\"/n.woff\");" +
      "font-weight:350;font-style:normal;font-display:swap}</style>",
  },
  {
    name: "a face without a source is dropped",
    fonts: [{ family: "NoSource" }],
    expected: "",
  },
  { name: "an empty record emits nothing", fonts: {}, expected: "" },
  { name: "undefined fonts emit nothing", fonts: undefined, expected: "" },
];

for (const fontCase of FONT_CASES) {
  test(`fonts: ${fontCase.name}`, async () => {
    assert.equal(await fontTags(fontCase.fonts), fontCase.expected);
  });
}

test("math: display mode wrapper, KaTeX markup and screenshot metadata", async () => {
  const { templateV2UiToHtmlFragment } = await loadTemplateV2HtmlRenderer();
  const fragment = templateV2UiToHtmlFragment({
    elements: [
      {
        type: "math",
        position: { x: 20, y: 30 },
        size: { width: 600, height: 120 },
        latex: String.raw`$$\frac{a}{b}$$`,
        display_mode: true,
        font: { size: 38, color: "#312E81" },
        alignment: { horizontal: "center", vertical: "middle" },
      },
    ],
  });
  const html = fragment.slice(SLIDE_ROOT_OPEN_TAG.length, -"</div>".length);

  // The KaTeX payload itself is library-owned; pin the renderer's own wrapper.
  assert.ok(
    html.startsWith(
      '<div data-presenton-math="true" data-screenshot="true"' +
        ' data-screenshot-include-children="true"' +
        ' aria-label="Mathematical expression: \\frac{a}{b}"' +
        ' style="box-sizing:border-box;min-height:0;min-width:0;position:absolute;' +
        "left:20px;top:30px;width:600px;height:120px;display:flex;" +
        "align-items:center;justify-content:center;overflow:hidden;" +
        'color:#312E81;font-size:38px;">' +
        '<div style="max-width:100%;max-height:100%;overflow:hidden;">',
    ),
    html.slice(0, 480),
  );
  assert.ok(html.endsWith("</div></div>"));
  assert.match(html, /<span class="katex-display">/);
  assert.match(html, /<math[^>]*display="block"/);
  assert.doesNotMatch(html, /\$\$/);
});

test("math: inline mode drops the display wrapper and uses the default font", async () => {
  const { templateV2UiToHtmlFragment } = await loadTemplateV2HtmlRenderer();
  const fragment = templateV2UiToHtmlFragment({
    elements: [
      {
        type: "math",
        position: { x: 0, y: 0 },
        size: { width: 100, height: 40 },
        latex: "x^2",
        display_mode: false,
      },
    ],
  });
  const html = fragment.slice(SLIDE_ROOT_OPEN_TAG.length, -"</div>".length);

  assert.ok(
    html.startsWith(
      '<div data-presenton-math="true" data-screenshot="true"' +
        ' data-screenshot-include-children="true"' +
        ' aria-label="Mathematical expression: x^2"' +
        ' style="box-sizing:border-box;min-height:0;min-width:0;position:absolute;' +
        "left:0px;top:0px;width:100px;height:40px;display:flex;" +
        "align-items:flex-start;justify-content:flex-start;overflow:hidden;" +
        'color:#111827;font-size:32px;">',
    ),
    html.slice(0, 480),
  );
  assert.doesNotMatch(html, /katex-display/);
  assert.match(html, /<span class="katex">/);
});
