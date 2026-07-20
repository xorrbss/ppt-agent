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
    (element) => "shape" in element && element.shape.shape === "round-rectangle"
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
  assert.ok(title.text.runs.length >= 2, "rich title should retain separate runs");
  assert.ok(
    title.text.style.cjkFallbackFamilies.includes("Noto Sans KR"),
    "CJK fallback stack should be explicit"
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
  assert.ok(
    slide.elements.some(
      (element) =>
        element.classification.mode === "raster" &&
        element.classification.reasons.includes("complex-table")
    )
  );
  assert.ok(
    slide.elements.some(
      (element) =>
        element.classification.mode === "raster" &&
        element.classification.reasons.includes("svg-text")
    )
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

  const rasterOnly = rasterElement(slide, "filter");
  assert.ok(rasterOnly);
  await assert.rejects(
    renderAuthoredBackplate(html, slide, [rasterOnly.id], options),
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
  for (const index of [3, 4, 5]) {
    const individualTransform = slide.elements.find(
      (element) => element.domPath === `body > p:nth-of-type(${index})`
    );
    assert.equal(individualTransform?.classification.mode, "raster");
    assert.ok(
      individualTransform?.classification.reasons.includes("complex-transform")
    );
  }
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
  assert.equal(ancestorTransform?.classification.mode, "raster");
  assert.ok(
    ancestorTransform?.classification.reasons.includes("transformed-ancestor")
  );
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

  for (const index of [6, 7]) {
    const unsafeRadius = slide.elements.find(
      (element) => element.domPath === `body > div:nth-of-type(${index})`
    );
    assert.equal(unsafeRadius?.classification.mode, "raster");
    assert.ok(unsafeRadius?.classification.reasons.includes("unsupported-shape"));
  }

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
