import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

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
import { assembleAuthoredHybridPptx } from "./pptx-assembler.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";
import { preflightAuthoredHtmlForHybrid } from "./security.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, "..", "..", "..", "..");
const FIXTURES = [
  "e2e-text-table-shapes.html",
  "e2e-cjk-9pt-lines-long-text.html",
  "e2e-image-svg-hybrid.html",
];

const CONTENT_TYPES = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="png" ContentType="image/png"/>
</Types>`;
const PRESENTATION = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>`;
const SLIDE = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:pic><p:nvPicPr><p:cNvPr id="2" name="v0.4.2 fidelity raster"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId2"/></p:blipFill></p:pic></p:spTree></p:cSld></p:sld>`;
const RELS = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image.png"/></Relationships>`;

function fidelitySkeleton(slideCount) {
  const entries = new Map([
    ["[Content_Types].xml", Buffer.from(CONTENT_TYPES)],
    ["ppt/presentation.xml", Buffer.from(PRESENTATION)],
  ]);
  for (let slideNumber = 1; slideNumber <= slideCount; slideNumber += 1) {
    entries.set(`ppt/slides/slide${slideNumber}.xml`, Buffer.from(SLIDE));
    entries.set(
      `ppt/slides/_rels/slide${slideNumber}.xml.rels`,
      Buffer.from(RELS.replace("image.png", `fidelity-${slideNumber}.png`))
    );
    entries.set(
      `ppt/media/fidelity-${slideNumber}.png`,
      Buffer.from(`v0.4.2 fidelity raster ${slideNumber}`)
    );
  }
  return writePptxArchive(entries);
}

function sameIds(elements, ids) {
  return elements.length === ids.length &&
    elements.every((element, index) => element.source.id === ids[index]);
}

async function buildLayer(html, slideNumber, chromeExecutable) {
  const preflight = preflightAuthoredHtmlForHybrid(html);
  assert.equal(preflight.ok, true, preflight.reason);
  const options = { chromeExecutable, timeoutMs: 30_000 };
  const contract = await extractAuthoredSlideDom(html, options);
  const prepared = await prepareNativeElements(contract.elements, {
    includeRasterText: true,
    includeRasterShapes: true,
  });
  let selected = selectLayerSafeNativeElements(
    contract.elements,
    prepared,
    undefined,
    { promoteTextAboveRaster: true, promoteShapesAboveRaster: false }
  );
  assert.ok(selected.length > 0, `fixture ${slideNumber} produced no editable layer`);
  let backplate = await renderAuthoredBackplate(
    html,
    contract,
    selected.map((element) => element.source.id),
    options
  );
  const applied = new Set(backplate.appliedPromotedElementIds);
  const finalSelected = selectLayerSafeNativeElements(
    contract.elements,
    prepared,
    applied,
    { promoteTextAboveRaster: true, promoteShapesAboveRaster: false }
  );
  if (!sameIds(finalSelected, backplate.appliedPromotedElementIds)) {
    selected = finalSelected;
    backplate = await renderAuthoredBackplate(
      html,
      contract,
      selected.map((element) => element.source.id),
      options
    );
  } else {
    selected = finalSelected;
  }
  assert.ok(sameIds(selected, backplate.appliedPromotedElementIds));
  return {
    contract,
    layer: {
      slideNumber,
      backplatePng: backplate.backplatePng,
      elements: selected,
    },
  };
}

function slideXml(entries, slideNumber) {
  return entries.get(`ppt/slides/slide${slideNumber}.xml`).toString("utf8");
}

test("v0.4.2 authored-hybrid representative fixtures preserve editable structure end to end", async (t) => {
  const chromeExecutable = await resolveAuthoredHybridChromeExecutable();
  if (!chromeExecutable) {
    if (process.env.CI === "true") assert.fail("CI must provide Chrome/Chromium");
    t.skip("Chrome/Chromium is unavailable");
    return;
  }

  const [rootPackage, installedVersion, ...htmlSlides] = await Promise.all([
    fs.readFile(path.join(REPO_ROOT, "package.json"), "utf8").then(JSON.parse),
    fs.readFile(path.join(REPO_ROOT, "presentation-export", ".installed-version"), "utf8"),
    ...FIXTURES.map((fixture) =>
      fs.readFile(path.join(HERE, "fixtures", fixture), "utf8")
    ),
  ]);
  assert.equal(rootPackage.presentationExportVersion, "v0.4.2");
  assert.equal(installedVersion.trim(), "v0.4.2");

  // No v0.3.3 runtime is retained in the repository. A binary byte comparison
  // would require downloading or replacing the pinned runtime, so the stable
  // baseline is the fidelity skeleton contract plus semantic OOXML invariants.
  const baseline = fidelitySkeleton(htmlSlides.length);
  const baselineEntries = readPptxArchive(baseline);
  for (let slideNumber = 1; slideNumber <= htmlSlides.length; slideNumber += 1) {
    assert.match(slideXml(baselineEntries, slideNumber), /v0\.4\.2 fidelity raster/);
  }

  const results = [];
  for (let index = 0; index < htmlSlides.length; index += 1) {
    results.push(await buildLayer(htmlSlides[index], index + 1, chromeExecutable));
  }
  const output = assembleAuthoredHybridPptx(
    baseline,
    results.map((result) => result.layer)
  );
  const entries = readPptxArchive(output);
  const xml1 = slideXml(entries, 1);
  const xml2 = slideXml(entries, 2);
  const xml3 = slideXml(entries, 3);

  for (const xml of [xml1, xml2, xml3]) {
    assert.doesNotMatch(xml, /v0\.4\.2 fidelity raster/);
    assert.match(xml, /Presenton hybrid/);
  }

  for (const text of [
    "Quarterly operating review",
    "Metric",
    "Revenue",
    "Retention",
    "All labels and values must remain editable",
  ]) {
    assert.ok(xml1.includes(text), `slide 1 lost editable text: ${text}`);
  }
  assert.match(xml1, /prst="(?:rect|roundRect|ellipse|line)"/);
  assert.ok(
    results[0].layer.elements.some((element) => element.kind === "shape"),
    "slide 1 must retain editable table borders or authored geometry"
  );

  for (const text of [
    "편집 가능한 한글 프레젠테이션",
    "최소 글꼴 기준 검증",
    "이 문장은 완전하게 보존되어야 합니다.",
    "가로선과 세로선",
  ]) {
    assert.ok(xml2.includes(text), `slide 2 lost editable CJK text: ${text}`);
  }
  assert.match(xml2, /\bsz="900"/, "authored sub-9pt text must clamp to 9pt");
  const lineElements = results[1].layer.elements.filter(
    (element) => element.kind === "shape" && element.source.shape.shape === "line"
  );
  assert.ok(lineElements.length >= 2, "both axis-aligned rules must stay editable");
  const lineTransforms = [...xml2.matchAll(/<a:xfrm[^>]*>[\s\S]*?<a:ext cx="(\d+)" cy="(\d+)"\/>[\s\S]*?<\/a:xfrm>/g)]
    .map((match) => [Number(match[1]), Number(match[2])]);
  assert.ok(
    lineTransforms.some(([cx, cy]) => cx > 1_000_000 && cy <= 100),
    "horizontal line must be serialized on an exact PowerPoint axis"
  );
  assert.ok(
    lineTransforms.some(([cx, cy]) => cy > 1_000_000 && cx <= 100),
    "vertical line must be serialized on an exact PowerPoint axis"
  );

  for (const text of [
    "Native media with residual artwork",
    "Input",
    "Output",
    "The bitmap and safe SVG geometry stay selectable",
  ]) {
    assert.ok(xml3.includes(text), `slide 3 lost editable content: ${text}`);
  }
  assert.match(xml3, /<p:pic>/, "native data image must remain a selectable picture");
  assert.ok(
    results[2].layer.elements.some((element) => element.kind === "image"),
    "slide 3 must promote the embedded bitmap"
  );
  assert.ok(
    results[2].layer.elements.some((element) => element.kind === "shape"),
    "slide 3 must promote safe SVG geometry"
  );
  const backplateStats = await sharp(results[2].layer.backplatePng)
    .extract({ left: 900, top: 120, width: 280, height: 280 })
    .stats();
  assert.ok(
    backplateStats.channels.slice(0, 3).some((channel) => channel.stdev > 10),
    "filtered/rotated artwork must remain visible in the residual backplate"
  );

  assert.equal(
    [...entries.keys()].filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name)).length,
    3
  );
  assert.ok(
    [...entries.keys()].filter((name) => /^ppt\/media\/hybrid-s\d+-backplate-/.test(name)).length >= 3,
    "each representative slide must receive a hybrid backplate"
  );
});
