import assert from "node:assert/strict";
import test from "node:test";

import { writePptxArchive } from "./pptx-archive.ts";
import { inspectPptxEditability } from "./pptx-quality-inspection.ts";

test("structural editability inspection covers all 20 slides", () => {
  const entries = new Map([["[Content_Types].xml", Buffer.from("<Types/>")]]);
  for (let slideNumber = 1; slideNumber <= 20; slideNumber += 1) {
    const native =
      slideNumber <= 18
        ? '<p:sp><p:nvSpPr/><p:spPr/><p:txBody/></p:sp>'
        : "";
    entries.set(
      `ppt/slides/slide${slideNumber}.xml`,
      Buffer.from(
        `<p:sld><p:cSld><p:spTree><p:pic><p:nvPicPr><p:cNvPr name="Presenton hybrid backplate"/></p:nvPicPr></p:pic>${native}</p:spTree></p:cSld></p:sld>`
      )
    );
  }
  const report = inspectPptxEditability(writePptxArchive(entries));
  assert.equal(report.totalSlides, 20);
  assert.equal(report.editableSlides, 18);
  assert.equal(report.imageFallbackSlides, 2);
  assert.equal(report.nativeTextElements, 18);
  assert.equal(report.nativeGroupElements, 0);
  assert.equal(report.rasterFallbackElements, 20);
  assert.deepEqual(
    report.slides
      .filter((slide) => slide.imageFallback)
      .map((slide) => slide.slideNumber),
    [19, 20]
  );
  assert.equal(
    report.fallbackReasonCounts["residual-backplate-present"],
    20
  );
});

test("structural inspection distinguishes groups, connectors, small images, and full-slide fallback", () => {
  const entries = new Map([
    ["[Content_Types].xml", Buffer.from("<Types/>")],
    [
      "ppt/presentation.xml",
      Buffer.from(
        '<p:presentation><p:sldSz cx="12192000" cy="6858000"/></p:presentation>'
      ),
    ],
    [
      "ppt/slides/slide1.xml",
      Buffer.from(
        '<p:sld><p:cSld><p:spTree>' +
          '<p:pic><p:nvPicPr><p:cNvPr id="2" name="Presenton hybrid backplate"/></p:nvPicPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="12192000" cy="6858000"/></a:xfrm></p:spPr></p:pic>' +
          '<p:grpSp><p:nvGrpSpPr/><p:grpSpPr/><p:sp><p:nvSpPr/><p:spPr/></p:sp></p:grpSp>' +
          '<p:cxnSp><p:nvCxnSpPr/><p:spPr/></p:cxnSp>' +
          '<p:sp><p:nvSpPr/><p:spPr/><p:txBody/></p:sp>' +
          '<p:pic><p:nvPicPr><p:cNvPr id="7" name="logo"/></p:nvPicPr><p:spPr><a:xfrm><a:off x="10" y="10"/><a:ext cx="1000" cy="1000"/></a:xfrm></p:spPr></p:pic>' +
          "</p:spTree></p:cSld></p:sld>"
      ),
    ],
    [
      "ppt/slides/slide2.xml",
      Buffer.from(
        '<p:sld><p:cSld><p:spTree><p:pic><p:nvPicPr><p:cNvPr id="9" name="Full slide"/></p:nvPicPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="12192000" cy="6858000"/></a:xfrm></p:spPr></p:pic></p:spTree></p:cSld></p:sld>'
      ),
    ],
  ]);
  const report = inspectPptxEditability(writePptxArchive(entries));
  assert.equal(report.slides[0].nativeTextElements, 1);
  assert.equal(report.slides[0].nativeShapeElements, 2);
  assert.equal(report.slides[0].nativeGroupElements, 1);
  assert.equal(report.slides[0].nativeImageElements, 1);
  assert.equal(report.slides[0].rasterFallbackElements, 1);
  assert.equal(report.slides[1].editable, false);
  assert.equal(report.slides[1].imageFallback, true);
  assert.equal(report.slides[1].nativeImageElements, 0);
  assert.deepEqual(report.slides[1].fallbackReasons, [
    "full-slide-image-fallback",
  ]);
});
