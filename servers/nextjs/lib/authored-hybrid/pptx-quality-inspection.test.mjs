import assert from "node:assert/strict";
import test from "node:test";

import { writePptxArchive } from "./pptx-archive.ts";
import { inspectPptxEditability } from "./pptx-quality-inspection.ts";
import { createAuthoredNativeDispositionReceipt } from "./native-disposition.ts";
import {
  boundedReleaseRasterOnly,
  EDITABLE_HYBRID_SHADOW_PROFILE_ID,
  presentationRequiresRasterOnlyHybrid,
} from "./presentation-mode.ts";

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
  assert.equal(report.rasterFallbackElements, 0);
  assert.equal(report.backplateCount, 20);
  assert.equal(report.ooxmlReadback.status, "passed");
  assert.deepEqual(
    report.slides
      .filter((slide) => slide.imageFallback)
      .map((slide) => slide.slideNumber),
    [19, 20]
  );
  assert.equal(report.fallbackReasonCounts["residual-backplate-present"], undefined);
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
  assert.equal(report.slides[0].rasterFallbackElements, 0);
  assert.equal(report.slides[0].backplateCount, 1);
  assert.equal(report.slides[1].editable, false);
  assert.equal(report.slides[1].imageFallback, true);
  assert.equal(report.slides[1].nativeImageElements, 0);
  assert.deepEqual(report.slides[1].fallbackReasons, [
    "full-slide-image-fallback",
  ]);
});

test("semantic raster ownership carriers do not inflate native shape coverage", () => {
  const entries = new Map([
    ["[Content_Types].xml", Buffer.from("<Types/>")],
    [
      "ppt/slides/slide1.xml",
      Buffer.from(
        '<p:sld><p:cSld><p:spTree>' +
          '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Presenton raster visual fragment s01-title semantic-owner" hidden="1"/></p:nvSpPr><p:spPr/></p:sp>' +
          '<p:sp><p:nvSpPr><p:cNvPr id="4" name="Presenton raster visual fragment s01-chart semantic-owner" hidden="1"/></p:nvSpPr><p:spPr/></p:sp>' +
          '</p:spTree></p:cSld></p:sld>',
      ),
    ],
  ]);
  const report = inspectPptxEditability(writePptxArchive(entries));
  assert.equal(report.slides[0].nativeShapeElements, 0);
  assert.equal(report.slides[0].editable, false);
  assert.equal(report.slides[0].imageFallback, true);
});

test("explicit Hybrid admits supported native owners and reports QID-native ratio with fallback reasons", () => {
  assert.equal(boundedReleaseRasterOnly(undefined), false);
  assert.equal(
    boundedReleaseRasterOnly(EDITABLE_HYBRID_SHADOW_PROFILE_ID),
    false,
  );
  assert.equal(boundedReleaseRasterOnly("unknown-profile"), true);
  assert.equal(
    presentationRequiresRasterOnlyHybrid({
      mode: "authored",
      theme: {
        authored_vnext: {
          profileId: "unknown-profile",
          status: "blocked",
        },
      },
    }),
    true,
  );
  assert.equal(
    presentationRequiresRasterOnlyHybrid({
      mode: "authored",
      theme: { authored_vnext: { compositionPin: { digest: "a" } } },
    }),
    false,
  );

  const receipt = createAuthoredNativeDispositionReceipt([{
    slideNumber: 1,
    editable: true,
    imageFallback: false,
    nativeTextElements: 2,
    nativeShapeElements: 1,
    nativeImageElements: 0,
    nativeTableElements: 1,
    nativeChartElements: 1,
    rasterFallbackElements: 1,
    logicalCoverage: {
      nativeText: 2,
      nativeImage: 0,
      nativeShape: 1,
      nativeChart: 1,
      nativeGroup: 0,
      rasterRemaining: 1,
    },
    ooxmlReadback: {
      status: "not-run",
      nativeText: 0,
      nativeImage: 0,
      nativeShape: 0,
      nativeChart: 0,
      nativeGroup: 0,
      rasterRemaining: 0,
    },
    backplateCount: 1,
    typographyIssues: [],
    fallbackReasons: ["unsupported-chart-css"],
    fallbackElements: [{
      elementId: "chart.complex",
      candidateKind: "complex",
      reasons: ["unsupported-chart-css"],
    }],
    qidCoverage: [
      { elementId: "title", kind: "text", logical: "native", ooxml: "not-run", com: "not-run" },
      { elementId: "body", kind: "text", logical: "native", ooxml: "not-run", com: "not-run" },
      { elementId: "shape", kind: "shape", logical: "native", ooxml: "not-run", com: "not-run" },
      { elementId: "table", kind: "table", logical: "native", ooxml: "not-run", com: "not-run" },
      { elementId: "chart", kind: "chart", logical: "native", ooxml: "not-run", com: "not-run" },
      { elementId: "chart.complex", kind: "chart", logical: "raster", ooxml: "not-run", com: "not-run" },
    ],
  }]);

  assert.equal(receipt.status, "passed");
  assert.equal(receipt.nativeElements, 5);
  assert.equal(receipt.rasterFallbackElements, 1);
  assert.equal(receipt.nativeRatioBasisPoints, 8333);
  assert.equal(receipt.nativeRatio, 0.8333);
  assert.deepEqual(receipt.nativeByKind, {
    text: 2,
    image: 0,
    shape: 1,
    table: 1,
    chart: 1,
  });
  assert.deepEqual(receipt.fallbackReasonCounts, {
    "unsupported-chart-css": 1,
  });
  assert.deepEqual(receipt.slides[0].elements.at(-1), {
    elementId: "title",
    kind: "text",
    disposition: "native",
    fallbackReasons: [],
  });
});
