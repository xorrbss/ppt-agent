import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import sharp from "sharp";

import {
  extractAuthoredSlideDom,
  renderAuthoredBackplate,
  resolveAuthoredHybridChromeExecutable,
} from "./index.ts";
import {
  prepareNativeElements,
  selectLayerSafeNativeElements,
} from "./native-plan.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE_PATH = path.join(HERE, "fixtures", "hybrid-v1.html");

function textElement(slide, plainText) {
  return slide.elements.find(
    (element) => "text" in element && element.text.plainText.includes(plainText)
  );
}

function rasterElement(slide, reason) {
  return slide.elements.find(
    (element) =>
      element.classification.mode === "raster" &&
      element.classification.reasons.includes(reason)
  );
}

async function rgbaAt(png, x, y) {
  const { data, info } = await sharp(png).ensureAlpha().raw().toBuffer({
    resolveWithObject: true,
  });
  const offset = (y * info.width + x) * info.channels;
  return [...data.subarray(offset, offset + 4)];
}

async function changedPixels(pngA, pngB, region) {
  const [left, top, width, height] = region;
  const [a, b] = await Promise.all([
    sharp(pngA).extract({ left, top, width, height }).ensureAlpha().raw().toBuffer(),
    sharp(pngB).extract({ left, top, width, height }).ensureAlpha().raw().toBuffer(),
  ]);
  let changed = 0;
  for (let index = 0; index < a.length; index += 4) {
    if (
      a[index] !== b[index] ||
      a[index + 1] !== b[index + 1] ||
      a[index + 2] !== b[index + 2] ||
      a[index + 3] !== b[index + 3]
    ) {
      changed += 1;
    }
  }
  return changed;
}

test("Chrome extracts candidates and produces subset-aware RGBA backplates", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") {
      assert.fail("CI must provide Chrome/Chromium for the authored hybrid smoke test");
    }
    t.skip("Chrome/Chromium is unavailable; unit contract tests still run");
    return;
  }

  const html = await fs.readFile(FIXTURE_PATH, "utf8");
  const options = { chromeExecutable, timeoutMs: 30_000 };
  const slide = await extractAuthoredSlideDom(html, options);

  assert.equal(slide.source.viewport.widthPx, 1280);
  assert.equal(slide.source.viewport.heightPx, 720);
  assert.equal(slide.source.viewport.widthIn, 13.333333);
  assert.equal(slide.source.viewport.heightIn, 7.5);

  const title = textElement(slide, "성과 보고서");
  const body = textElement(slide, "리치 텍스트");
  const numeric = textElement(slide, "+24.8%");
  const caption = textElement(slide, "H1 고정 fixture");
  const safeImage = slide.elements.find(
    (element) => "image" in element && element.image.alt === "safe red pixel"
  );
  const safeShape = slide.elements.find(
    (element) =>
      element.classification.mode === "native" &&
      element.classification.kind === "shape" &&
      element.shape.shape === "round-rectangle"
  );
  const shapeDiagnostics = slide.elements.filter(
    (element) =>
      (element.classification.mode === "native" &&
        element.classification.kind === "shape") ||
      (element.classification.mode === "raster" &&
        element.classification.candidateKind === "shape")
  );

  for (const [name, element] of Object.entries({
    title,
    body,
    numeric,
    caption,
    safeImage,
    safeShape,
  })) {
    assert.ok(
      element,
      `missing ${name} candidate: ${JSON.stringify(shapeDiagnostics)}`
    );
    assert.equal(element.classification.mode, "native", `${name} should be native`);
  }
  assert.equal(title.text.role, "title");
  assert.equal(body.text.role, "body");
  assert.equal(numeric.text.role, "numeric");
  assert.equal(caption.text.role, "caption");
  assert.equal(
    body.text.style.wrapMode,
    "wrap",
    "normal CSS text should retain PowerPoint wrapping as an overflow safety net"
  );
  assert.ok(
    body.text.runs.some((run) => run.text === "\n"),
    "a browser soft wrap should become an explicit editable line break"
  );
  assert.ok(title.text.runs.length >= 2, "rich title should retain separate runs");
  assert.ok(
    title.text.style.fontFamilies.includes("Malgun Gothic"),
    "CSS capture should include the same local fallback used by PowerPoint"
  );
  assert.equal(
    title.text.style.cjkFallbackFamilies[0],
    "Malgun Gothic",
    "the CJK fallback should match the PowerPoint compatibility policy"
  );

  const expectedFallbackReasons = [
    "filter",
    "clip-path",
    "pseudo-element",
    "overflow-clipped",
    "occluded",
    "complex-transform",
    "mask",
    "mix-blend-mode",
    "backdrop-filter",
    "unsupported-opacity",
    "unknown-z-order",
    "unsupported-shape",
  ];
  for (const reason of expectedFallbackReasons) {
    assert.ok(rasterElement(slide, reason), `missing raster fallback reason ${reason}`);
  }
  const editableTableCell = textElement(slide, "complex");
  assert.ok(editableTableCell, "table cell text should be extracted independently");
  assert.ok(
    editableTableCell.text.containerShape,
    "table cell border/fill should be retained as an editable container shape"
  );
  assert.ok(
    slide.elements.some(
      (element) =>
        element.tagName === "rect" &&
        "shape" in element &&
        element.shape.shape === "rectangle"
    ),
    "decomposable SVG geometry should retain an editable shape payload"
  );

  const baselineResult = await renderAuthoredBackplate(html, slide, [], options);
  const titleOnlyResult = await renderAuthoredBackplate(html, slide, [title.id], options);
  const titleAndImageResult = await renderAuthoredBackplate(
    html,
    slide,
    [title.id, safeImage.id],
    options
  );
  const shapeOnlyResult = await renderAuthoredBackplate(
    html,
    slide,
    [safeShape.id],
    options
  );
  const baseline = baselineResult.backplatePng;
  const titleOnly = titleOnlyResult.backplatePng;
  const titleAndImage = titleAndImageResult.backplatePng;
  const shapeOnly = shapeOnlyResult.backplatePng;
  assert.deepEqual(titleAndImageResult.appliedPromotedElementIds, [
    title.id,
    safeImage.id,
  ]);
  assert.deepEqual(titleAndImageResult.fallbackElementIds, []);
  const metadata = await sharp(titleAndImage).metadata();
  assert.equal(metadata.format, "png");
  assert.equal(metadata.width, 1280);
  assert.equal(metadata.height, 720);
  assert.equal(metadata.hasAlpha, true);
  assert.deepEqual(await rgbaAt(titleAndImage, 5, 5), [0, 0, 0, 0]);

  assert.ok(
    (await changedPixels(baseline, titleOnly, [120, 100, 500, 80])) > 25,
    "promoted text glyphs should disappear without removing the card"
  );
  assert.equal(
    await changedPixels(baseline, titleOnly, [620, 260, 140, 110]),
    0,
    "an omitted native image must stay rasterised on the backplate"
  );
  assert.ok(
    (await changedPixels(titleOnly, titleAndImage, [620, 260, 140, 110])) > 10_000,
    "only the successfully promoted image should be suppressed"
  );
  assert.deepEqual(await rgbaAt(titleAndImage, 690, 315), [244, 241, 234, 255]);
  assert.ok(
    (await changedPixels(baseline, shapeOnly, [480, 310, 120, 44])) > 2_000,
    "promoted simple shape should disappear while its card remains"
  );
  assert.deepEqual(await rgbaAt(shapeOnly, 540, 332), [244, 241, 234, 255]);

  await assert.rejects(
    renderAuthoredBackplate(html, slide, ["not-an-eligible-element"], options),
    /raster-only/
  );
  await assert.rejects(
    renderAuthoredBackplate(`${html}\n<!-- changed -->`, slide, [], options),
    /source fingerprint/
  );
  await assert.rejects(
    renderAuthoredBackplate(html, slide, [title.id, title.id], options),
    /duplicates/
  );
  await assert.rejects(
    renderAuthoredBackplate(html, slide, [], {
      ...options,
      baseUrl: "https://example.invalid/deck/",
    }),
    /baseUrl/
  );
});

test("Chrome extracts direct labels beside nested flex children", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    .step{display:flex;align-items:center;gap:12px;margin:80px;width:260px;height:48px}
    .step-num{display:flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:50%;background:#2878d8;color:white}
  </style></head><body><div class="step"><div class="step-num">1</div>Data lookup</div></body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const label = textElement(slide, "Data lookup");
  const number = textElement(slide, "1");

  assert.ok(label, "direct flex-item text should be materialized and extracted");
  assert.equal(label.tagName, "span");
  assert.equal(label.classification.mode, "native");
  assert.ok(number, "the nested number remains independently editable");
});

test("Chrome backplate capture is not blocked by a pending image decode", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:transparent}
    img{position:absolute;left:40px;top:40px;width:120px;height:80px}
  </style></head><body>
    <img alt="pending decode" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4oKDwHwAFZAIwtYA8zQAAAABJRU5ErkJggg==">
    <script>HTMLImageElement.prototype.decode=function(){return new Promise(function(){})}</script>
  </body></html>`;
  const options = { chromeExecutable, timeoutMs: 30_000 };
  const slide = await extractAuthoredSlideDom(html, options);
  const image = slide.elements.find(
    (element) => "image" in element && element.image.alt === "pending decode"
  );
  assert.ok(image);
  assert.equal(image.classification.mode, "native");

  const result = await renderAuthoredBackplate(html, slide, [image.id], options);
  assert.deepEqual(result.appliedPromotedElementIds, [image.id]);
  assert.deepEqual(result.fallbackElementIds, []);
  assert.deepEqual(await rgbaAt(result.backplatePng, 100, 80), [0, 0, 0, 0]);
});

test("Chrome rejects changed element identity without failing the whole backplate", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    p{position:absolute;left:80px;top:80px;font:32px/40px Arial;color:rgb(20,30,40)}
  </style></head><body><p data-ppt-role="body"></p><script>
    document.querySelector("p").textContent = location.pathname;
  </script></body></html>`;
  const options = { chromeExecutable, timeoutMs: 30_000 };
  const slide = await extractAuthoredSlideDom(html, options);
  const text = slide.elements.find(
    (element) =>
      element.classification.mode === "native" &&
      element.classification.kind === "text"
  );
  assert.ok(text);

  const result = await renderAuthoredBackplate(html, slide, [text.id], options);
  assert.deepEqual(result.appliedPromotedElementIds, []);
  assert.deepEqual(result.fallbackElementIds, [text.id]);
  assert.equal((await sharp(result.backplatePng).metadata()).width, 1280);
});

test("Chrome fails closed for SVG occlusion, wide-gamut color, and ambiguous stacking", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    p{position:absolute;margin:0;font:24px/32px Arial}
  </style></head><body>
    <p data-ppt-role="body" style="left:40px;top:40px">behind svg</p>
    <svg style="position:absolute;left:35px;top:35px;width:180px;height:50px"><rect width="180" height="50" fill="white"/></svg>
    <p data-ppt-role="body" style="left:40px;top:130px;color:color(display-p3 1 0 0)">wide gamut</p>
    <p data-ppt-role="body" style="left:40px;top:210px;z-index:2">stacked</p>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  assert.ok(rasterElement(slide, "occluded"));
  assert.ok(rasterElement(slide, "unsupported-color"));
  assert.ok(rasterElement(slide, "unknown-z-order"));
});

test("Chrome fails closed for pointer-transparent paint and off-center rotation", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    p{position:absolute;margin:0;font:24px/32px Arial}
  </style></head><body>
    <p data-ppt-role="body" style="left:40px;top:40px">pointer-transparent cover</p>
    <div style="position:absolute;left:35px;top:35px;width:300px;height:50px;background:white;pointer-events:none"></div>
    <p data-ppt-role="body" style="left:100px;top:140px;width:100px;height:40px;transform:rotate(90deg);transform-origin:0 0">off-center rotation</p>
    <p data-ppt-role="body" style="left:100px;top:280px;rotate:20deg">individual rotate</p>
    <p data-ppt-role="body" style="left:100px;top:360px;translate:20px 0">individual translate</p>
    <p data-ppt-role="body" style="left:100px;top:440px;scale:1.2">individual scale</p>
    <p data-ppt-role="body" style="left:40px;top:500px;width:400px">partially covered text</p>
    <div style="position:absolute;left:40px;top:495px;width:18px;height:42px;background:black"></div>
    <p data-ppt-role="body" style="left:100px;top:635px;width:140px">shadow covered</p>
    <div style="position:absolute;left:280px;top:630px;width:20px;height:30px;box-shadow:-120px 0 35px 25px rgba(0,0,0,.85)"></div>
    <div style="position:absolute;left:500px;top:50px;translate:40px 0"><p data-ppt-role="body" style="position:static">translated ancestor</p></div>
    <p data-ppt-role="body" style="left:500px;top:150px">descendant <span style="display:inline-block;rotate:10deg">rotate</span></p>
    <ul style="position:absolute;left:500px;top:500px"><li>default marker</li></ul>
    <img alt="decorated" style="position:absolute;left:800px;top:520px;width:20px;height:20px;padding:2px;background:blue" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=">
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const coveredText = slide.elements.find(
    (element) => element.domPath === "body > p:nth-of-type(1)"
  );
  const offCenterRotation = slide.elements.find(
    (element) => element.domPath === "body > p:nth-of-type(2)"
  );

  assert.equal(coveredText?.classification.mode, "raster");
  assert.ok(coveredText?.classification.reasons.includes("occluded"));
  assert.equal(offCenterRotation?.classification.mode, "raster");
  assert.ok(offCenterRotation?.classification.reasons.includes("complex-transform"));
  assert.deepEqual(offCenterRotation?.bounds.px, {
    x: 60,
    y: 140,
    width: 40,
    height: 100,
  });
  for (const index of [3, 5]) {
    const individualTransform = slide.elements.find(
      (element) => element.domPath === `body > p:nth-of-type(${index})`
    );
    assert.equal(individualTransform?.classification.mode, "raster");
    assert.ok(
      individualTransform?.classification.reasons.includes("complex-transform")
    );
  }
  const translated = slide.elements.find(
    (element) => element.domPath === "body > p:nth-of-type(4)"
  );
  assert.equal(translated?.classification.mode, "native");
  assert.equal(translated?.bounds.px.x, 120);
  for (const index of [6, 7]) {
    const covered = slide.elements.find(
      (element) => element.domPath === `body > p:nth-of-type(${index})`
    );
    assert.equal(covered?.classification.mode, "raster");
    assert.ok(covered?.classification.reasons.includes("occluded"));
  }
  const ancestorTransform = slide.elements.find(
    (element) =>
      element.domPath === "body > div:nth-of-type(4) > p:nth-of-type(1)"
  );
  assert.equal(ancestorTransform?.classification.mode, "native");
  const descendantTransform = slide.elements.find(
    (element) => element.domPath === "body > p:nth-of-type(8)"
  );
  assert.equal(descendantTransform?.classification.mode, "raster");
  assert.ok(
    descendantTransform?.classification.reasons.includes("complex-transform")
  );
  const listItem = slide.elements.find(
    (element) => element.domPath === "body > ul:nth-of-type(1) > li:nth-of-type(1)"
  );
  assert.equal(listItem?.classification.mode, "raster");
  assert.ok(listItem?.classification.reasons.includes("pseudo-element"));
  const decoratedImage = slide.elements.find(
    (element) => element.domPath === "body > img:nth-of-type(1)"
  );
  assert.equal(decoratedImage?.classification.mode, "raster");
  assert.ok(decoratedImage?.classification.reasons.includes("decorated-image"));
});

test("Chrome accumulates rigid ancestor rotation and rejects non-rigid or clipped ancestry", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    section{position:absolute;width:190px;height:100px}
    section > div{position:absolute;left:35px;top:25px;width:100px;height:40px;background:#2878d8}
    .rigid{left:40px;top:40px;transform:rotate(8deg)}
    .rigid > div{transform:rotate(4deg)}
    .scale{left:270px;top:40px;transform:scale(1.15)}
    .skew{left:500px;top:40px;transform:skewX(8deg)}
    .off-center{left:730px;top:40px;transform:rotate(8deg);transform-origin:0 0}
    .perspective{left:960px;top:40px;perspective:600px}
    .clip{left:40px;top:230px;width:90px;overflow:hidden}
    .clip > div{left:55px}
  </style></head><body>
    <section class="rigid"><div></div></section>
    <section class="scale"><div></div></section>
    <section class="skew"><div></div></section>
    <section class="off-center"><div></div></section>
    <section class="perspective"><div></div></section>
    <section class="clip"><div></div></section>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const shape = (index) =>
    slide.elements.find(
      (element) =>
        element.domPath ===
        `body > section:nth-of-type(${index}) > div:nth-of-type(1)`
    );

  const rigid = shape(1);
  assert.equal(rigid?.classification.mode, "native");
  assert.ok(Math.abs(rigid.rotationDeg - 12) < 0.01);
  assert.ok(Math.abs(rigid.bounds.px.width - 100) < 0.1);
  assert.ok(Math.abs(rigid.bounds.px.height - 40) < 0.1);
  assert.ok(
    !rigid.classification.reasons?.includes("transformed-ancestor")
  );

  for (const index of [2, 3, 4]) {
    const nonRigid = shape(index);
    assert.equal(nonRigid?.classification.mode, "raster");
    assert.ok(nonRigid?.classification.reasons.includes("transformed-ancestor"));
  }

  const perspective = shape(5);
  assert.equal(perspective?.classification.mode, "raster");
  assert.ok(perspective?.classification.reasons.includes("unknown-z-order"));

  const clipped = shape(6);
  assert.equal(clipped?.classification.mode, "raster");
  assert.ok(clipped?.classification.reasons.includes("overflow-clipped"));
});

test("Chrome fails closed for descendant effects and paint outside element bounds", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    p{position:absolute;margin:0;width:220px;font:20px/30px Arial}
    @keyframes pulse{from{opacity:1}to{opacity:.8}}
    .pseudo-cover::before{content:"";position:absolute;left:-260px;top:5px;width:18px;height:32px;background:#fff}
  </style></head><body>
    <p data-ppt-role="body" style="left:40px;top:40px">outline paint</p>
    <div style="position:absolute;left:360px;top:40px;width:10px;height:30px;outline:240px solid #111"></div>
    <p data-ppt-role="body" style="left:40px;top:120px">text shadow paint</p>
    <div style="position:absolute;left:340px;top:120px;width:40px;height:30px;font:20px Arial;text-shadow:-220px 0 30px #111">FX</div>
    <p data-ppt-role="body" style="left:40px;top:200px">filter paint</p>
    <div style="position:absolute;left:340px;top:200px;width:20px;height:30px;background:#111;filter:drop-shadow(-220px 0 30px #111)"></div>
    <p data-ppt-role="body" style="left:40px;top:280px">pseudo paint</p>
    <div class="pseudo-cover" style="position:absolute;left:300px;top:275px;width:10px;height:10px"></div>

    <p data-ppt-role="body" style="left:600px;top:40px">vertical <span style="writing-mode:vertical-rl">run</span></p>
    <p data-ppt-role="body" style="left:600px;top:100px">animated <span style="display:inline-block;animation:pulse 2s infinite">run</span></p>
    <p data-ppt-role="body" style="left:600px;top:160px">columns <span style="display:inline-block;column-count:2">a b</span></p>
    <p data-ppt-role="body" style="left:600px;top:220px">stacked <span style="position:relative;z-index:2">run</span></p>
    <p data-ppt-role="body" style="left:600px;top:280px">isolated <span style="isolation:isolate">run</span></p>

    <img alt="rounded" style="position:absolute;left:1050px;top:40px;width:20px;height:20px;border-radius:0 24px" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=">
    <div data-ppt-role="shape" style="position:absolute;left:1050px;top:120px;width:120px;height:44px;background:#06c;outline:2px solid red"></div>
    <div data-ppt-role="shape" style="position:absolute;left:1050px;top:220px;width:120px;height:44px;background:#06c;border-radius:9999px"></div>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });

  for (const index of [1, 2, 3, 4]) {
    const covered = slide.elements.find(
      (element) => element.domPath === `body > p:nth-of-type(${index})`
    );
    assert.equal(covered?.classification.mode, "raster");
    assert.ok(covered?.classification.reasons.includes("occluded"));
  }
  const descendantReasons = [
    "vertical-writing",
    "animated",
    "css-columns",
    "unknown-z-order",
    "unknown-z-order",
  ];
  descendantReasons.forEach((reason, offset) => {
    const element = slide.elements.find(
      (candidate) => candidate.domPath === `body > p:nth-of-type(${offset + 5})`
    );
    assert.equal(element?.classification.mode, "raster");
    assert.ok(element?.classification.reasons.includes(reason));
  });
  const roundedImage = slide.elements.find(
    (element) => element.domPath === "body > img:nth-of-type(1)"
  );
  assert.equal(roundedImage?.classification.mode, "raster");
  assert.ok(roundedImage?.classification.reasons.includes("rounded-image"));
  const outlinedShape = slide.elements.find(
    (element) => element.domPath === "body > div:nth-of-type(5)"
  );
  assert.equal(outlinedShape?.classification.mode, "raster");
  assert.ok(outlinedShape?.classification.reasons.includes("unsupported-shape"));
  const pill = slide.elements.find(
    (element) => element.domPath === "body > div:nth-of-type(6)"
  );
  assert.equal(pill?.classification.mode, "native");
  assert.equal(pill?.classification.kind, "shape");
  assert.equal(pill && "shape" in pill ? pill.shape.shape : undefined, "round-rectangle");
  assert.equal(pill && "shape" in pill ? pill.shape.radiusPt : undefined, 16.5);
});

test("Chrome closes pseudo, external-paint, radius, and rotated-geometry regressions", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    body{position:relative}
    p{position:absolute;margin:0;width:220px;font:20px/30px Arial}
    .ancestor-risk{position:absolute;left:450px;top:40px;width:280px;height:60px;box-shadow:0 0 12px #111}
    .ancestor-risk::before{content:"";position:absolute;inset:0;border:2px solid #111}
    .relative-cover::before{content:"";display:inline-block;position:relative;left:-260px;width:220px;height:36px;background:#111}
    .high-cover::before{content:"";position:absolute;inset:0;background:#111;z-index:999}
    .outline-cover::before{content:"";position:absolute;inset:0;outline:240px solid #111}
    .shadow-cover::before{content:"FX";position:absolute;inset:0;font:20px Arial;text-shadow:-220px 0 30px #111}
  </style></head><body>
    <div class="ancestor-risk"><p data-ppt-role="body" style="position:static">related ancestor paint</p></div>
    <p data-ppt-role="body" style="left:450px;top:120px">descendant paint <span style="outline:2px solid #111">edge</span></p>

    <p data-ppt-role="body" style="left:40px;top:200px">relative pseudo cover</p>
    <div class="relative-cover" style="position:absolute;left:300px;top:195px;width:10px;height:10px"></div>
    <div class="high-cover" style="position:absolute;left:40px;top:275px;width:220px;height:40px"></div>
    <p data-ppt-role="body" style="left:40px;top:280px">earlier high pseudo cover</p>
    <p data-ppt-role="body" style="left:40px;top:360px">pseudo outline cover</p>
    <div class="outline-cover" style="position:absolute;left:300px;top:355px;width:10px;height:30px"></div>
    <p data-ppt-role="body" style="left:40px;top:440px">pseudo shadow cover</p>
    <div class="shadow-cover" style="position:absolute;left:300px;top:435px;width:20px;height:30px"></div>

    <div data-ppt-role="shape" style="position:absolute;left:800px;top:40px;width:120px;height:44px;background:#06c;border-radius:100% / 50%"></div>
    <div data-ppt-role="shape" style="position:absolute;left:950px;top:40px;width:120px;height:44px;background:#06c;border-radius:4px 8px"></div>
    <div data-ppt-role="shape" style="position:absolute;left:800px;top:160px;width:120px;height:44px;background:#06c;border-radius:9999px;transform:rotate(10deg)"></div>
    <img alt="rotated cover" style="position:absolute;left:1000px;top:160px;width:120px;height:44px;object-fit:cover;transform:rotate(10deg)" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=">
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });

  const relatedAncestor = slide.elements.find(
    (element) => element.domPath === "body > div:nth-of-type(1) > p:nth-of-type(1)"
  );
  assert.equal(relatedAncestor?.classification.mode, "raster");
  assert.ok(relatedAncestor?.classification.reasons.includes("pseudo-element"));
  assert.ok(relatedAncestor?.classification.reasons.includes("external-paint"));

  const descendantPaint = slide.elements.find(
    (element) => element.domPath === "body > p:nth-of-type(1)"
  );
  assert.equal(descendantPaint?.classification.mode, "raster");
  assert.ok(descendantPaint?.classification.reasons.includes("external-paint"));

  for (const index of [2, 3, 4, 5]) {
    const covered = slide.elements.find(
      (element) => element.domPath === `body > p:nth-of-type(${index})`
    );
    assert.equal(covered?.classification.mode, "raster");
    assert.ok(covered?.classification.reasons.includes("occluded"));
  }

  const ellipticalRadius = slide.elements.find(
    (element) => element.domPath === "body > div:nth-of-type(6)"
  );
  assert.equal(ellipticalRadius?.classification.mode, "raster");
  assert.ok(ellipticalRadius?.classification.reasons.includes("unsupported-shape"));

  const asymmetricRadius = slide.elements.find(
    (element) => element.domPath === "body > div:nth-of-type(7)"
  );
  assert.equal(asymmetricRadius?.classification.mode, "native");
  assert.equal(asymmetricRadius?.classification.kind, "shape");
  assert.equal(
    asymmetricRadius && "shape" in asymmetricRadius
      ? asymmetricRadius.shape.shape
      : undefined,
    "freeform"
  );
  assert.ok(
    asymmetricRadius && "shape" in asymmetricRadius
      ? (asymmetricRadius.shape.points?.length ?? 0) >= 12
      : false
  );

  const rotatedPill = slide.elements.find(
    (element) => element.domPath === "body > div:nth-of-type(8)"
  );
  assert.equal(rotatedPill?.classification.mode, "native");
  assert.equal(rotatedPill?.classification.kind, "shape");
  assert.equal(rotatedPill && "shape" in rotatedPill ? rotatedPill.shape.radiusPt : undefined, 16.5);
  assert.equal(rotatedPill?.bounds.px.width, 120);
  assert.equal(rotatedPill?.bounds.px.height, 44);

  const rotatedImage = slide.elements.find(
    (element) => "image" in element && element.image.alt === "rotated cover"
  );
  assert.ok(rotatedImage);
  assert.equal(rotatedImage.classification.mode, "native");
  assert.equal(rotatedImage.image.crop.left, 0);
  assert.equal(rotatedImage.image.crop.right, 0);
  assert.equal(rotatedImage.image.crop.top, 0.316667);
  assert.equal(rotatedImage.image.crop.bottom, 0.316667);
});

test("backplate capture rejects style-only identity drift", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const stylesheetDirectory = await fs.mkdtemp(
    path.join(os.tmpdir(), "authored-hybrid-style-")
  );
  t.after(() => fs.rm(stylesheetDirectory, { recursive: true, force: true }));
  const stylesheetPath = path.join(stylesheetDirectory, "style.css");
  await fs.writeFile(stylesheetPath, "p{color:rgb(255,0,0)}", "utf8");
  const baseUrl = pathToFileURL(`${stylesheetDirectory}${path.sep}`).href;
  const html = `<!doctype html><html><head>
    <link rel="stylesheet" href="style.css">
    <style>html,body{width:1280px;height:720px;margin:0;overflow:hidden}p{position:absolute;left:80px;top:80px;margin:0;font:32px/40px Arial}</style>
  </head><body><p data-ppt-role="body">stable text</p></body></html>`;
  const options = { chromeExecutable, timeoutMs: 30_000, baseUrl };
  const slide = await extractAuthoredSlideDom(html, options);
  const text = textElement(slide, "stable text");
  assert.ok(text);
  assert.equal(text.text.style.color.hex, "FF0000");

  await fs.writeFile(stylesheetPath, "p{color:rgb(0,0,255)}", "utf8");
  const result = await renderAuthoredBackplate(
    html,
    slide,
    [text.id],
    options
  );
  assert.deepEqual(result.appliedPromotedElementIds, []);
  assert.deepEqual(result.fallbackElementIds, [text.id]);
});

test("vertical text anchor honors flex-direction", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") {
      assert.fail("CI must provide Chrome/Chromium for anchor coverage");
    }
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><meta charset="utf-8"><style>
    html,body{margin:0;width:1280px;height:720px;background:#fff}
    .box{position:absolute;width:400px;height:160px;display:flex;
         font:700 30px Arial,sans-serif;color:#123}
    #row{left:40px;top:40px;justify-content:center;align-items:flex-start}
    #col{left:40px;top:240px;flex-direction:column;justify-content:center}
    #cross{left:40px;top:440px;align-items:center}
  </style>
  <div class="box" id="row" data-ppt-role="title">가로중앙 ROW</div>
  <div class="box" id="col" data-ppt-role="title">세로중앙 COL</div>
  <div class="box" id="cross" data-ppt-role="title">교차중앙 CROSS</div>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const row = textElement(slide, "가로중앙 ROW");
  const col = textElement(slide, "세로중앙 COL");
  const cross = textElement(slide, "교차중앙 CROSS");
  assert.ok(row && col && cross);
  // Row flex: justify-content centers the HORIZONTAL axis, so the vertical anchor
  // stays top (the old bug mapped it to middle).
  assert.equal(row.text.style.verticalAlignment, "top");
  // Column flex: justify-content is the main (vertical) axis -> middle.
  assert.equal(col.text.style.verticalAlignment, "middle");
  // Row flex: align-items centers the cross (vertical) axis -> middle.
  assert.equal(cross.text.style.verticalAlignment, "middle");
});

test("Chrome preserves computed text box geometry, alignment provenance, and line-break kinds", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    *{box-sizing:content-box}
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    .outer{position:absolute;left:80px;top:60px;text-align:right}
    .contract{width:220px;margin:11px 13px 17px 19px;padding:7px 23px 11px 5px;
      border-style:solid;border-width:1px 2px 3px 4px;
      font:400 18px/27px Arial;letter-spacing:2px}
    .contract strong{font-weight:700;color:#2457d6;font-size:20px}
    .flex{position:absolute;left:500px;top:80px;width:260px;height:100px;
      display:flex;flex-direction:column;align-items:flex-end;justify-content:center;
      row-gap:9px;column-gap:13px;font:16px/22px Arial;text-align:center;white-space:nowrap}
  </style></head><body>
    <div class="outer"><p class="contract">Alpha <strong>bold words</strong> plus enough words to wrap in this narrow content box.<br>Hard line.</p></div>
    <div class="flex">Centered flex label</div>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const contract = textElement(slide, "Alpha");
  const flex = textElement(slide, "Centered flex label");
  assert.ok(contract && flex);

  assert.equal(contract.text.style.horizontalAlignment, "right");
  assert.equal(contract.text.style.lineHeight.points, 20.25);
  assert.equal(contract.text.style.letterSpacingPt, 1.5);
  assert.deepEqual(contract.text.layout?.paddingPx, {
    top: 7,
    right: 23,
    bottom: 11,
    left: 5,
  });
  assert.deepEqual(contract.text.layout?.borderPx, {
    top: 1,
    right: 2,
    bottom: 3,
    left: 4,
  });
  assert.deepEqual(contract.text.layout?.marginPx, {
    top: 11,
    right: 13,
    bottom: 17,
    left: 19,
  });
  assert.deepEqual(contract.text.layout?.paragraphSpacingPx, {
    before: 0,
    after: 0,
  });
  assert.equal(contract.text.layout?.boxBounds.px.width, 254);
  assert.equal(contract.text.layout?.contentBounds.px.width, 220);
  assert.equal(
    contract.text.layout?.contentBounds.px.x,
    contract.text.layout?.boxBounds.px.x + 9
  );
  assert.deepEqual(contract.bounds, contract.text.layout?.contentBounds);
  assert.ok(contract.text.layout?.paintedTextBounds);
  assert.equal(contract.text.layout?.textAlignSource, "inherited");
  assert.ok((contract.text.layout?.lineCount ?? 0) >= 3);
  assert.equal(contract.text.layout?.singleLine, false);
  const breakKinds = new Set(
    contract.text.runs
      .filter((run) => run.text === "\n")
      .map((run) => run.breakKind)
  );
  assert.ok(breakKinds.has("soft"), "automatic wrapping must remain distinguishable");
  assert.ok(breakKinds.has("line"), "authored <br> must remain distinguishable");
  const bold = contract.text.runs.find((run) => run.text.includes("bold words"));
  assert.ok(bold);
  assert.equal(bold.style.bold, true);
  assert.equal(bold.style.fontWeight, 700);
  assert.equal(bold.style.fontSizePt, 15);
  assert.equal(bold.style.color.hex, "2457D6");

  assert.equal(flex.text.style.horizontalAlignment, "center");
  assert.equal(flex.text.style.verticalAlignment, "middle");
  assert.equal(flex.text.layout?.display, "flex");
  assert.equal(flex.text.layout?.flexDirection, "column");
  assert.equal(flex.text.layout?.alignItems, "flex-end");
  assert.equal(flex.text.layout?.justifyContent, "center");
  assert.equal(flex.text.layout?.rowGapPx, 9);
  assert.equal(flex.text.layout?.columnGapPx, 13);
  assert.equal(flex.text.layout?.textAlignSource, "self");
  assert.equal(flex.text.layout?.lineCount, 1);
  assert.equal(flex.text.layout?.singleLine, true);
});

test("pretty-printed inline HTML does not create phantom PowerPoint lines", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    *{box-sizing:border-box}
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    p{position:absolute;left:80px;top:80px;width:480px;margin:0;font:20px/32px Arial}
    .metric{position:absolute;left:80px;top:180px;width:100px;margin:0;font:900 42px/.82 Arial;letter-spacing:-.07em}
    .metric small{display:block;margin-top:9px;font:9px/1 Arial;letter-spacing:.12em;text-align:right}
    .wrapped{position:absolute;left:240px;top:180px;width:210px;margin:0;font:16px/24px Arial}
    .principle{position:absolute;left:520px;top:180px;display:flex;gap:10px;font:16px/24px Arial}
    .padded{position:absolute;left:520px;top:260px;width:300px;padding:8px 18px;border-left:2px solid #fc3;font:16px/24px Arial}
  </style></head><body>
    <p data-ppt-role="body">
      Generated code is fast, but
      <strong>review and approval</strong>
      must stay on the same browser-computed lines.
    </p>
    <div class="metric" data-ppt-role="numeric">7<small>review steps</small></div>
    <p class="wrapped">Browser wrapping must remain editable without crossing the authored text box.</p>
    <div class="principle">reuse <span>→</span> approve <span>→</span> evidence</div>
    <p class="padded">Padded text starts after its CSS inset.</p>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const paragraph = textElement(slide, "Generated code is fast");
  const metric = textElement(slide, "review steps");
  const wrapped = textElement(slide, "Browser wrapping");
  const principle = textElement(slide, "reuse");
  const padded = textElement(slide, "Padded text");
  assert.ok(paragraph && metric && wrapped && principle && padded);
  assert.doesNotMatch(paragraph.text.plainText, /\n\s*review and approval/);
  assert.equal(
    paragraph.text.runs.map((run) => run.text).join("").replace(/\n/g, " "),
    paragraph.text.plainText
  );
  assert.equal(metric.text.plainText.trim(), "7\nreview steps");
  assert.equal(
    metric.text.runs.map((run) => run.text).join("").trim(),
    "7\nreview steps"
  );
  assert.equal(metric.text.runs.find((run) => run.text.includes("review steps"))?.style.fontSizePt, 9);
  assert.equal(metric.text.style.wrapMode, "wrap");
  assert.ok(
    wrapped.text.runs.some((run) => run.text === "\n"),
    "browser automatic wrapping should become an explicit editable line"
  );
  assert.equal(
    principle.text.plainText.replace(/\s+/g, " ").trim(),
    "reuse → approve → evidence",
    "row flex items must not become vertical PowerPoint paragraphs"
  );
  assert.equal(padded.bounds.px.x, 540);
  assert.equal(padded.bounds.px.width, 262);
  assert.deepEqual(padded.text.containerShape?.shape.borderLines, [
    {
      side: "left",
      color: { hex: "FFCC33", alpha: 1 },
      widthPt: 1.5,
    },
  ]);
});

test("Chrome suppresses decorated raster text selected by editable export", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    p{position:absolute;left:80px;top:80px;margin:0;font:32px/40px Arial;color:#123;text-shadow:1px 1px #fff}
  </style></head><body><p data-ppt-role="body">decorated editable text</p></body></html>`;
  const options = { chromeExecutable, timeoutMs: 30_000 };
  const slide = await extractAuthoredSlideDom(html, options);
  const text = textElement(slide, "decorated editable text");
  assert.ok(text);
  assert.equal(text.classification.mode, "raster");
  assert.ok(slide.backplate.eligibleElementIds.includes(text.id));

  const result = await renderAuthoredBackplate(html, slide, [text.id], options);
  assert.deepEqual(result.appliedPromotedElementIds, [text.id]);
  assert.deepEqual(result.fallbackElementIds, []);
});

test("inline-only mixed-style prose is one editable text root while highlight paint stays on the backplate", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    *{box-sizing:border-box}
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    .quote{position:absolute;left:80px;top:80px;width:270px;margin:0;font:700 24px/34px Arial;color:#182235}
    .quote em{font-style:normal;color:#2457d6;background:linear-gradient(transparent 58%,#ffb21a 0)}
    .quote span{font-weight:400;color:#687385}
    .node{position:absolute;left:420px;top:80px;z-index:1;width:27px;height:27px;display:inline-flex;align-items:center;justify-content:center;background:#182235;border:1.5px solid #7697f1;border-radius:50%;outline:2px solid #2457d6;font:850 12px/1 Arial;color:#afc5ff}
  </style></head><body>
    <div class="quote">Lead words <em>highlighted words</em><span> and regular tail</span></div>
    <span class="node">01</span>
  </body></html>`;
  const options = { chromeExecutable, timeoutMs: 30_000 };
  const slide = await extractAuthoredSlideDom(html, options);
  const quoteElements = slide.elements.filter(
    (element) =>
      "text" in element &&
      element.text.plainText.includes("Lead words") &&
      element.bounds.px.x < 400
  );
  assert.equal(quoteElements.length, 1, "inline prose must not become overlapping sibling text boxes");
  const quote = quoteElements[0];
  assert.equal(quote.tagName, "div");
  assert.match(
    quote.text.plainText.replace(/\n/g, " "),
    /^Lead words highlighted words and regular tail$/
  );
  assert.equal(quote.text.style.bold, true);
  const highlighted = quote.text.runs.find((run) => run.text.includes("highlighted"));
  const regular = quote.text.runs.find((run) => run.text.includes("regular tail"));
  assert.ok(highlighted && regular);
  assert.equal(highlighted.style.bold, true);
  assert.equal(highlighted.style.color.hex, "2457D6");
  assert.equal(regular.style.bold, false);
  assert.equal(regular.style.color.hex, "687385");
  const node = textElement(slide, "01");
  assert.ok(node);
  assert.equal(node.text.containerShape?.shape.shape, "ellipse");
  assert.equal(node.text.containerShape?.bounds.px.width, 27);
  assert.equal(node.text.containerShape?.bounds.px.height, 27);
  assert.deepEqual(node.text.containerShape?.shape.outline, {
    color: { hex: "2457D6", alpha: 1 },
    widthPt: 1.5,
    offsetPx: 0,
  });

  const prepared = await prepareNativeElements(slide.elements, { includeRasterText: true });
  const selected = selectLayerSafeNativeElements(
    slide.elements,
    prepared,
    new Set([quote.id, node.id]),
    { promoteTextAboveRaster: true }
  );
  assert.deepEqual(selected.map((item) => item.source.id), [quote.id, node.id]);

  const result = await renderAuthoredBackplate(html, slide, [quote.id, node.id], options);
  assert.deepEqual(result.appliedPromotedElementIds, [quote.id, node.id]);
  assert.deepEqual(result.fallbackElementIds, []);
  const { data } = await sharp(result.backplatePng).ensureAlpha().raw().toBuffer({
    resolveWithObject: true,
  });
  let yellowPixels = 0;
  for (let index = 0; index < data.length; index += 4) {
    if (
      data[index] >= 245 &&
      data[index + 1] >= 145 &&
      data[index + 1] <= 205 &&
      data[index + 2] <= 60 &&
      data[index + 3] >= 245
    ) {
      yellowPixels += 1;
    }
  }
  assert.ok(yellowPixels > 250, "highlight decoration should remain behind native text");
});

test("Chrome promotes editable cards, circles, and thin dividers without removing nested text", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    .card{position:absolute;left:80px;top:80px;width:420px;height:170px;background:#eef5ff;border:2px solid #2f6bca;border-radius:24px}
    .card p{margin:28px 32px;font:28px/38px Arial;color:#123}
    .badge{position:absolute;left:560px;top:80px;padding:16px 24px;background:#102a56;border:1px solid #68a4ff;border-radius:18px;font:24px/32px Arial;color:#fff}
    .circle{position:absolute;left:80px;top:310px;width:96px;height:96px;border-radius:50%;background:#5b8def;border:3px solid #153b75}
    .line{position:absolute;left:230px;top:356px;width:310px;height:3px;background:#2f6bca}
  </style></head><body>
    <div class="card"><p data-ppt-role="body">Nested editable text</p></div>
    <div class="badge" data-ppt-role="caption">Editable badge</div>
    <div class="circle"></div><div class="line"></div>
  </body></html>`;
  const options = { chromeExecutable, timeoutMs: 30_000 };
  const slide = await extractAuthoredSlideDom(html, options);
  const card = slide.elements.find(
    (element) => "shape" in element && element.shape.shape === "round-rectangle" && element.shape.preserveContents
  );
  const circle = slide.elements.find(
    (element) => "shape" in element && element.shape.shape === "ellipse"
  );
  const divider = slide.elements.find(
    (element) =>
      "shape" in element &&
      element.shape.shape === "rectangle" &&
      Math.abs(element.bounds.px.height - 3) < 0.1
  );
  const nestedText = textElement(slide, "Nested editable text");
  const badge = textElement(slide, "Editable badge");

  assert.ok(card && circle && divider && nestedText && badge);
  assert.equal(card.classification.mode, "native");
  assert.equal(circle.classification.mode, "native");
  assert.equal(divider.classification.mode, "native");
  assert.equal(nestedText.classification.mode, "native");
  assert.equal(badge.classification.mode, "raster");
  assert.ok(slide.backplate.eligibleElementIds.includes(badge.id));
  assert.equal(badge.text.containerShape?.shape.shape, "round-rectangle");

  const promotedIds = [card.id, circle.id, divider.id, nestedText.id, badge.id];
  const result = await renderAuthoredBackplate(html, slide, promotedIds, options);
  assert.deepEqual(result.appliedPromotedElementIds, promotedIds);
  assert.deepEqual(result.fallbackElementIds, []);
  assert.deepEqual(await rgbaAt(result.backplatePng, 100, 100), [255, 255, 255, 255]);
});

test("HTML diagram and timeline containers retain editable descendant text", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    .diagram{position:absolute;left:40px;top:40px;width:560px;height:260px;background:#eef5ff}
    .card{position:absolute;left:24px;top:24px;width:240px;height:110px;background:#fff;border:1px solid #9ab}
    .card h3{margin:18px 20px 6px;font:700 24px/30px Arial}.card p{margin:0 20px;font:16px/22px Arial}
    .timeline{position:absolute;left:640px;top:40px;width:560px;height:260px;background:#f4f7fb}
    .stage{display:inline-block;margin:30px 24px}.yr{font:700 18px/24px Arial}.task{font:16px/22px Arial}
  </style></head><body>
    <section class="diagram"><article class="card"><h3>Editable card title</h3><p>Editable card detail</p></article></section>
    <div class="timeline"><div class="stage"><div class="yr">1차년도</div><div class="task">업무 가동<br>로그 축적</div></div></div>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });

  const diagram = slide.elements.find(
    (element) =>
      element.classification.mode === "raster" &&
      element.classification.candidateKind === "complex" &&
      element.domPath.endsWith("section:nth-of-type(1)")
  );
  assert.ok(diagram, "diagram paint remains on the raster backplate");
  assert.ok(textElement(slide, "Editable card title"));
  assert.ok(textElement(slide, "Editable card detail"));
  assert.ok(textElement(slide, "1차년도"));
  assert.ok(textElement(slide, "업무 가동\n로그 축적"));
});

test("SVG diagrams expose editable geometry and text descendants", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    svg{position:absolute;left:100px;top:100px;width:700px;height:300px}
    text{font:700 24px Arial;fill:#102a56}
  </style></head><body>
    <svg viewBox="0 0 700 300" xmlns="http://www.w3.org/2000/svg">
      <rect x="10" y="10" width="680" height="280" rx="20" fill="#eef5ff"/>
      <text x="60" y="90">신청서 에이전트</text>
      <text x="60" y="150"><tspan>정책 Gate</tspan></text>
    </svg>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });

  const svgRect = slide.elements.find(
    (element) =>
      element.tagName === "rect" &&
      "shape" in element &&
      element.shape.shape === "round-rectangle"
  );
  assert.ok(svgRect, "SVG panel geometry should be exported as an editable shape");
  assert.ok(textElement(slide, "신청서 에이전트"));
  assert.ok(textElement(slide, "정책 Gate"));
});

test("SVG drop shadows do not collapse an editable illustration into one bitmap", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#f7fafc}
    svg{position:absolute;left:200px;top:100px;width:600px;height:400px}
  </style></head><body>
    <svg viewBox="0 0 600 400" xmlns="http://www.w3.org/2000/svg">
      <defs><filter id="soft"><feDropShadow dx="0" dy="8" stdDeviation="12" flood-opacity=".18"/></filter></defs>
      <g filter="url(#soft)">
        <rect x="40" y="40" width="260" height="180" rx="24" fill="#ffffff" stroke="#2878d8"/>
        <circle cx="390" cy="130" r="72" fill="#dff6ef" stroke="#44b7a2"/>
      </g>
    </svg>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });

  const editableSvgShapes = slide.elements.filter(
    (element) =>
      ["rect", "circle"].includes(element.tagName) &&
      element.classification.mode === "native" &&
      element.classification.kind === "shape"
  );
  assert.equal(editableSvgShapes.length, 2);
  assert.equal(
    slide.elements.some(
      (element) =>
        element.tagName === "svg" &&
        element.classification.mode === "raster" &&
        element.classification.candidateKind === "complex"
    ),
    false,
    "the SVG root should not become one residual illustration"
  );
});

test("complex SVG illustrations become selectable pictures while compact connector arrowheads retain authored positions", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const complexPaths = Array.from({ length: 25 }, (_, index) =>
    `<path d="M${index * 6} 0h5v5h-5z" fill="#2878d8"/>`
  ).join("");
  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#f7fafc}
    svg{position:absolute}
  </style></head><body>
    <svg id="illustration" style="left:40px;top:40px;width:300px;height:120px" viewBox="0 0 300 120">${complexPaths}</svg>
    <svg id="connectors" style="left:100px;top:240px;width:500px;height:160px" viewBox="0 0 500 160" fill="none">
      <path d="M20 80 H300" stroke="#2878d8" stroke-width="3"/>
      <path d="M288 80 l-8 -5 v10 z" fill="#2878d8"/>
      <circle cx="300" cy="80" r="7" fill="#fff" stroke="#2878d8" stroke-width="3"/>
    </svg>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });

  const illustration = slide.elements.find(
    (element) =>
      element.tagName === "svg" &&
      element.domPath.includes("svg:nth-of-type(1)") &&
      "image" in element
  );
  assert.ok(illustration, "a path-heavy illustration should become one selectable picture");
  assert.equal(illustration.classification.mode, "native");
  assert.match(illustration.image.src, /^data:image\/png;base64,/);

  const baseline = await renderAuthoredBackplate(html, slide, [], {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const promoted = await renderAuthoredBackplate(html, slide, [illustration.id], {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  assert.ok(
    (await changedPixels(baseline.backplatePng, promoted.backplatePng, [40, 40, 300, 120])) > 500,
    "the promoted illustration should be removed from the residual backplate"
  );

  const arrowhead = slide.elements.find(
    (element) =>
      element.tagName === "path" &&
      "shape" in element &&
      element.shape.closed === true &&
      element.shape.fill?.hex === "2878D8"
  );
  assert.ok(arrowhead, "the compact connector arrowhead remains editable");
  assert.ok(
    Math.abs(arrowhead.bounds.px.x + arrowhead.bounds.px.width - 388) < 0.75,
    `expected authored arrow tip at x=388, got ${arrowhead.bounds.px.x + arrowhead.bounds.px.width}`
  );
});

test("large semantic HTML visuals become selectable pictures while their labels remain editable", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const nodes = Array.from({ length: 12 }, (_, index) =>
    `<span class="node" style="left:${40 + (index % 4) * 110}px;top:${40 + Math.floor(index / 4) * 80}px"></span>`
  ).join("");
  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#f7fafc}
    .visual{position:absolute;left:320px;top:100px;width:560px;height:380px;border-radius:50%;background:#eaf4ff}
    .visual::before{content:"";position:absolute;inset:35px;border:2px dashed #6aa8ef;border-radius:50%}
    .node{position:absolute;width:82px;height:48px;border:2px solid #2878d8;border-radius:12px;background:#fff}
    .label{position:absolute;left:175px;top:165px;font:700 24px Arial;color:#102a56}
    .inline-diagram{position:absolute;left:120px;top:250px;width:320px;height:80px}
  </style></head><body>
    <div class="visual" aria-label="Security agent ecosystem">
      ${nodes}
      <div class="label">AI Agent</div>
      <svg class="inline-diagram" viewBox="0 0 320 80">
        <path d="M5 60 H315" stroke="#2878d8" stroke-width="4"/>
        <text x="40" y="48" fill="#ff0000" font-size="32">SVG Label</text>
      </svg>
    </div>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });

  const visual = slide.elements.find(
    (element) =>
      element.tagName === "div" &&
      "image" in element &&
      element.image.alt === "Security agent ecosystem"
  );
  assert.ok(visual, "the semantic visual should become one selectable picture");
  assert.equal(visual.classification.mode, "native");
  assert.match(visual.image.src, /^data:image\/png;base64,/);
  const label = textElement(slide, "AI Agent");
  assert.ok(label, "labels inside the visual should remain editable text");
  assert.ok("text" in label);
  const svgLabel = textElement(slide, "SVG Label");
  assert.ok(svgLabel, "SVG labels inside the visual should remain editable text");

  const illustrationPng = Buffer.from(visual.image.src.split(",")[1], "base64");
  const { data: illustrationPixels } = await sharp(illustrationPng)
    .ensureAlpha()
    .raw()
    .toBuffer({ resolveWithObject: true });
  let redGlyphPixels = 0;
  for (let index = 0; index < illustrationPixels.length; index += 4) {
    if (
      illustrationPixels[index] > 200 &&
      illustrationPixels[index + 1] < 80 &&
      illustrationPixels[index + 2] < 80 &&
      illustrationPixels[index + 3] > 80
    ) redGlyphPixels += 1;
  }
  assert.equal(redGlyphPixels, 0, "SVG glyph paint must be absent from the illustration picture");

  const prepared = await prepareNativeElements(slide.elements, {
    includeRasterText: true,
    includeRasterShapes: true,
  });
  const selected = selectLayerSafeNativeElements(
    slide.elements,
    prepared,
    new Set(prepared.map((item) => item.source.id)),
    { promoteTextAboveRaster: true, promoteShapesAboveRaster: true }
  );
  assert.ok(
    selected.some((item) => item.kind === "image" && item.source.id === visual.id),
    "the semantic visual should remain selected when extracted child nodes overlap it"
  );

  const baseline = await renderAuthoredBackplate(html, slide, [], {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const promoted = await renderAuthoredBackplate(html, slide, [visual.id], {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  assert.ok(
    (await changedPixels(baseline.backplatePng, promoted.backplatePng, [320, 100, 560, 380])) > 500,
    "the promoted semantic visual should be removed from the residual backplate"
  );
});

test("connector paths and detached arrowheads preserve authored SVG coordinates", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    .diagram{position:relative;width:800px;height:400px}
    svg{position:absolute;inset:0;width:800px;height:400px}
    .port{position:absolute;left:402px;top:121px;width:16px;height:16px;box-sizing:border-box;border:3px solid #2878d8;border-radius:50%;background:#fff}
  </style></head><body><div class="diagram">
    <svg viewBox="0 0 800 400" fill="none">
      <path d="M100 150 H410" stroke="#2878d8" stroke-width="3"/>
      <path d="M398 150 l-8 -5 v10 z" fill="#2878d8"/>
    </svg>
    <span class="port"></span>
  </div></body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const connector = slide.elements.find(
    (element) => element.tagName === "path" && "shape" in element && element.shape.closed === false
  );
  const arrowhead = slide.elements.find(
    (element) => element.tagName === "path" && "shape" in element && element.shape.closed === true
  );
  assert.ok(connector);
  assert.ok(arrowhead);
  const connectorEnd = connector.shape.points.at(-1);
  const connectorEndX = connector.bounds.px.x + connector.bounds.px.width * connectorEnd.x;
  const connectorEndY = connector.bounds.px.y + connector.bounds.px.height * connectorEnd.y;
  assert.ok(Math.abs(connectorEndX - 410) < 0.75, `expected authored connector x=410, got x=${connectorEndX}`);
  assert.ok(Math.abs(connectorEndY - 150) < 0.75, `expected authored connector y=150, got y=${connectorEndY}`);
  assert.ok(
    Math.abs(arrowhead.bounds.px.x + arrowhead.bounds.px.width - 398) < 0.75,
    `expected authored arrow tip at x=398, got ${arrowhead.bounds.px.x + arrowhead.bounds.px.width}`
  );
});

test("SVG connector paint preserves authored dashes, rounded caps, joins, and marker arrows", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    svg{position:absolute;left:40px;top:40px;width:600px;height:300px}
  </style></head><body>
    <svg viewBox="0 0 600 300" fill="none">
      <defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0 0 L10 5 L0 10 Z" fill="#2878d8"/></marker></defs>
      <path d="M20 80 C120 20 220 140 340 80" stroke="#2878d8" stroke-width="3" stroke-dasharray="6 7" stroke-linecap="round" stroke-linejoin="round" marker-end="url(#arrow)"/>
    </svg>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const connector = slide.elements.find(
    (element) => element.tagName === "path" && "shape" in element && element.shape.closed === false
  );
  assert.ok(connector);
  assert.equal(connector.shape.dash, "dash");
  assert.equal(connector.shape.lineCap, "round");
  assert.equal(connector.shape.lineJoin, "round");
  assert.equal(connector.shape.endArrow, "triangle");
});

test("CSS border-triangle arrows remain separate editable tips at authored coordinates", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    .arrow{position:absolute;left:100px;top:100px;width:80px;height:2px;background:#72aeef}
    .arrow::after{content:"";position:absolute;right:-1px;top:-4px;width:0;height:0;border-left:7px solid #2878d8;border-top:5px solid transparent;border-bottom:5px solid transparent}
  </style></head><body><div class="arrow"></div></body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const line = slide.elements.find(
    (element) => element.tagName === "div" && "shape" in element && element.shape.shape === "line"
  );
  const tip = slide.elements.find(
    (element) => element.tagName === "pseudo" && "shape" in element && element.shape.shape === "freeform"
  );
  assert.ok(line);
  assert.equal(line.shape.endArrow, undefined);
  assert.ok(tip);
  assert.equal(tip.shape.closed, true);
  assert.equal(tip.shape.fill.hex, "2878D8");
  assert.ok(Math.abs(tip.bounds.px.x + tip.bounds.px.width - 181) < 0.75);
  assert.ok(Math.abs(tip.bounds.px.y + tip.bounds.px.height / 2 - 101) < 0.75);
});

test("thin filled dividers remain rectangles instead of drifting PowerPoint lines", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    .horizontal{position:absolute;left:100px;top:100px;width:240px;height:2px;background:#2878d8}
    .vertical{position:absolute;left:400px;top:100px;width:3px;height:180px;background:#72aeef}
  </style></head><body><div class="horizontal"></div><div class="vertical"></div></body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const dividers = slide.elements.filter(
    (element) => element.tagName === "div" && "shape" in element
  );
  assert.equal(dividers.length, 2);
  assert.ok(dividers.every((element) => element.shape.shape === "rectangle"));
  assert.deepEqual(
    dividers.map((element) => element.shape.fill?.hex).sort(),
    ["2878D8", "72AEEF"]
  );
});

test("compact SVG icon strokes do not stretch to nearby card boundaries", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    svg{position:absolute;left:560px;top:140px;width:24px;height:24px}
    .card{position:absolute;left:610px;top:100px;width:220px;height:120px;border:2px solid #b9d7f7;border-radius:18px;background:#f7fafc}
  </style></head><body>
    <svg viewBox="0 0 24 24" fill="none"><path d="M4 12 H20" stroke="#2878d8" stroke-width="2"/></svg>
    <div class="card"></div>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const iconStroke = slide.elements.find(
    (element) => element.tagName === "path" && "shape" in element
  );
  assert.ok(iconStroke);
  assert.ok(
    iconStroke.bounds.px.width < 20,
    `compact icon stroke should retain its local width, got ${iconStroke.bounds.px.width}`
  );
  const end = iconStroke.shape.points.at(-1);
  const endX = iconStroke.bounds.px.x + iconStroke.bounds.px.width * end.x;
  assert.ok(Math.abs(endX - 580) < 0.75, `expected local icon endpoint at x=580, got ${endX}`);
});

test("rounded detached-arrow connectors retain their authored curve", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    .diagram{position:relative;width:800px;height:400px}
    svg{position:absolute;inset:0;width:800px;height:400px}
    .port{position:absolute;left:402px;top:121px;width:16px;height:16px;box-sizing:border-box;border:3px solid #2878d8;border-radius:50%;background:#fff}
  </style></head><body><div class="diagram">
    <svg viewBox="0 0 800 400" fill="none">
      <path d="M100 110 H340 Q380 110 380 140 V142 Q380 150 410 150" stroke="#2878d8" stroke-width="3"/>
      <path d="M398 150 l-8 -5 v10 z" fill="#2878d8"/>
    </svg>
    <span class="port"></span>
  </div></body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const connector = slide.elements.find(
    (element) => element.tagName === "path" && "shape" in element && element.shape.closed === false
  );
  assert.ok(connector);
  assert.ok(connector.shape.points.length > 8, `expected sampled curve, got ${connector.shape.points.length} points`);
  const points = connector.shape.points.map((point) => ({
    x: connector.bounds.px.x + connector.bounds.px.width * point.x,
    y: connector.bounds.px.y + connector.bounds.px.height * point.y,
  }));
  assert.ok(Math.abs(points[0].x - 100) < 0.75);
  assert.ok(Math.abs(points[0].y - 110) < 0.75);
  assert.ok(Math.abs(points.at(-1).x - 410) < 0.75);
  assert.ok(Math.abs(points.at(-1).y - 150) < 0.75);
  assert.ok(points.some((point) => point.x > 340 && point.x < 380 && point.y > 110 && point.y < 140));
});

test("painted cards expand just enough to retain overflowing flow content", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    .card{position:absolute;left:80px;top:80px;width:240px;height:100px;padding:12px;border:1px solid #bdd3ea;border-radius:18px;background:#fff}
    .row{height:40px;margin-bottom:8px}
    .tags{display:flex;flex-wrap:wrap;gap:5px}
    .tag{height:26px;padding:4px 8px;border-radius:6px;background:#eff5fb;font:12px/18px Arial}
  </style></head><body>
    <article class="card"><div class="row">Card title</div><div class="tags"><span class="tag">One</span><span class="tag">Two</span><span class="tag">Three</span></div></article>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const card = slide.elements.find(
    (element) => element.tagName === "article" && "shape" in element
  );
  assert.ok(card);
  assert.ok(card.bounds.px.height > 100, `expected card expansion, got ${card.bounds.px.height}`);
  assert.ok(card.bounds.px.height <= 136, `card expansion must stay bounded, got ${card.bounds.px.height}`);
});

test("connector paths and open chevron arrowheads keep authored card spacing", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    .diagram{position:relative;width:900px;height:400px}
    svg{position:absolute;inset:0;width:900px;height:400px}
    .card{position:absolute;left:610px;top:100px;width:220px;height:120px;border:2px solid #b9d7f7;border-radius:18px;background:#f7fafc}
  </style></head><body><div class="diagram">
    <svg viewBox="0 0 900 400" fill="none">
      <path d="M400 160 H560" stroke="#2878d8" stroke-width="3"/>
      <path d="M542 153 L560 160 L542 167" stroke="#2878d8" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    <div class="card"></div>
  </div></body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const openPaths = slide.elements.filter(
    (element) => element.tagName === "path" && "shape" in element && element.shape.closed === false
  );
  assert.equal(openPaths.length, 2);
  const connector = openPaths.find((element) => element.bounds.px.width > 100);
  const arrowhead = openPaths.find((element) => element.bounds.px.width < 40);
  assert.ok(connector && arrowhead);
  const connectorEnd = connector.shape.points.at(-1);
  const connectorEndX = connector.bounds.px.x + connector.bounds.px.width * connectorEnd.x;
  assert.ok(Math.abs(connectorEndX - 560) < 0.75, `expected authored connector endpoint, got ${connectorEndX}`);
  assert.ok(
    Math.abs(arrowhead.bounds.px.x + arrowhead.bounds.px.width - 560) < 0.75,
    `expected authored open arrow tip, got ${arrowhead.bounds.px.x + arrowhead.bounds.px.width}`
  );
});

test("generated flex decorations reserve space before editable direct text", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:#fff}
    .eyebrow{position:absolute;left:40px;top:40px;display:flex;align-items:center;gap:10px;font:700 16px/20px Arial;color:#2878d8}
    .eyebrow::before{content:"";width:26px;height:4px;background:#2878d8;flex:none}
    .chip{position:absolute;left:40px;top:100px;display:flex;align-items:center;padding:5px 8px;font:700 14px/18px Arial;color:#102a56}
    .chip::before{content:"";width:5px;height:5px;margin-right:6px;border-radius:50%;background:#2878d8;flex:none}
  </style></head><body>
    <div class="eyebrow">04 · Task 2</div>
    <div class="chip">Security platform DB</div>
  </body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const eyebrow = textElement(slide, "04 · Task 2");
  const chip = textElement(slide, "Security platform DB");
  assert.ok(eyebrow);
  assert.ok(chip);
  assert.ok(eyebrow.bounds.px.x >= 75, `expected eyebrow text after bar, got ${eyebrow.bounds.px.x}`);
  assert.ok(chip.bounds.px.x >= 58, `expected chip text after bullet, got ${chip.bounds.px.x}`);
  assert.ok(
    slide.elements.filter(
      (element) => element.tagName === "pseudo" && "shape" in element
    ).length >= 2,
    "the bar and bullet remain independent editable shapes"
  );
});

test("promoted SVG labels are removed from the raster backplate", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    svg{position:absolute;left:100px;top:100px;width:700px;height:300px}
    text{font:700 24px Arial;fill:#102a56}
  </style></head><body>
    <svg viewBox="0 0 700 300" xmlns="http://www.w3.org/2000/svg">
      <rect x="10" y="10" width="680" height="280" rx="20" fill="#eef5ff"/>
      <text x="60" y="90">Application Agent</text>
      <text x="60" y="150"><tspan>Policy Gate</tspan></text>
    </svg>
  </body></html>`;
  const options = { chromeExecutable, timeoutMs: 30_000 };
  const slide = await extractAuthoredSlideDom(html, options);
  const labels = [
    textElement(slide, "Application Agent"),
    textElement(slide, "Policy Gate"),
  ];
  assert.ok(labels.every(Boolean));

  const baseline = await renderAuthoredBackplate(html, slide, [], options);
  const promoted = await renderAuthoredBackplate(
    html,
    slide,
    labels.map((element) => element.id),
    options
  );
  assert.deepEqual(
    promoted.appliedPromotedElementIds,
    labels.map((element) => element.id)
  );
  assert.ok(
    (await changedPixels(
      baseline.backplatePng,
      promoted.backplatePng,
      [140, 150, 360, 120]
    )) > 100,
    "promoted SVG glyph paint is removed from the raster backplate"
  );
});

test("empty painted pseudo-elements become independently editable shapes", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden;background:transparent}
    .anchor{position:absolute;left:100px;top:100px;width:400px;height:200px}
    .anchor::before{content:"";position:absolute;left:30px;top:40px;width:240px;height:3px;background:#2878d8}
    .anchor::after{content:"";position:absolute;left:264px;top:34px;width:16px;height:16px;border-radius:50%;background:#2878d8}
    .child{position:absolute;left:0;top:0;width:20px;height:20px;background:#fff}
    .label{position:absolute;left:600px;top:100px;width:80px;height:40px}
    .label::after{content:"AI";display:flex;align-items:center;justify-content:center;width:80px;height:40px;border-radius:12px;background:#2878d8;color:#fff;font:700 20px Arial}
  </style></head><body><div class="anchor"><div class="child"></div></div><div class="label"></div></body></html>`;
  const options = { chromeExecutable, timeoutMs: 30_000 };
  const slide = await extractAuthoredSlideDom(html, options);
  const pseudos = slide.elements.filter(
    (element) => element.tagName === "pseudo" && "shape" in element
  );

  assert.equal(pseudos.length, 2);
  assert.deepEqual(
    pseudos.map((element) => element.shape.shape).sort(),
    ["ellipse", "line"]
  );
  const child = slide.elements.find((element) => element.domPath.endsWith("div:nth-of-type(1) > div:nth-of-type(1)"));
  const before = pseudos.find((element) => element.domPath.endsWith("::before"));
  const after = pseudos.find((element) => element.domPath.endsWith("::after"));
  assert.ok(child && before && after);
  assert.ok(before.sourceIndex <= child.sourceIndex, "::before paints before descendants");
  assert.ok(after.sourceIndex >= child.sourceIndex, "::after paints after descendants");
  const pseudoText = slide.elements.find(
    (element) =>
      element.tagName === "pseudo" &&
      "text" in element &&
      element.text.plainText === "AI"
  );
  assert.ok(pseudoText, "textual pseudo-element becomes editable text");

  const baseline = await renderAuthoredBackplate(html, slide, [], options);
  const promoted = await renderAuthoredBackplate(
    html,
    slide,
    pseudos.concat(pseudoText).map((element) => element.id),
    options
  );
  assert.deepEqual(
    promoted.appliedPromotedElementIds,
    pseudos.concat(pseudoText).map((element) => element.id)
  );
  assert.ok(
    (await changedPixels(baseline.backplatePng, promoted.backplatePng, [125, 130, 270, 40])) > 500,
    "promoting pseudo-element geometry removes its raster duplicate"
  );
  assert.equal((await rgbaAt(promoted.backplatePng, 150, 142))[3], 0);
  assert.equal((await rgbaAt(promoted.backplatePng, 372, 142))[3], 0);
});

test("overflow-hidden slide containers do not rasterize contained text", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") {
      assert.fail("CI must provide Chrome/Chromium for the authored hybrid smoke test");
    }
    t.skip("Chrome/Chromium is unavailable; unit contract tests still run");
    return;
  }

  const html = `<!doctype html><html><head><style>
    html,body{width:1280px;height:720px;margin:0;overflow:hidden}
    .slide{position:relative;width:1280px;height:720px;overflow:hidden}
    .safe{position:absolute;left:80px;top:80px;margin:0;font:32px/40px Arial}
    .clip{position:absolute;left:80px;top:180px;width:180px;height:40px;overflow:hidden}
    .clip p{position:static;width:420px;margin:0;white-space:nowrap;font:24px/36px Arial}
  </style></head><body><main class="slide">
    <p class="safe">Contained editable text</p>
    <div class="clip"><p>Actually clipped text remains raster</p></div>
  </main></body></html>`;
  const slide = await extractAuthoredSlideDom(html, {
    chromeExecutable,
    timeoutMs: 30_000,
  });
  const contained = textElement(slide, "Contained editable text");
  const clipped = rasterElement(slide, "overflow-clipped");

  assert.ok(contained);
  assert.equal(contained.classification.mode, "native");
  assert.ok(clipped);
  assert.equal(clipped.classification.mode, "raster");
});
