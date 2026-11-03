import assert from "node:assert/strict";
import test from "node:test";

import { frameElementShapeRefs, rasterPreservationLines, templateReason } from "./svg-template-status";
import type { SvgTemplateFrame } from "./svg-templates";

const bounds = { x: 0, y: 0, width: 10, height: 10 };
const frame: SvgTemplateFrame = {
  frame_id: "frame-5",
  elements: [
    { element_id: "bg", source_part: "ppt/slideMasters/slideMaster1.xml", source_shape_id: "background", kind: "rect", layer: "background", bounds },
    { element_id: "band", source_part: "ppt/slideLayouts/slideLayout4.xml", source_shape_id: "8,10", kind: "image", layer: "foreground", bounds,
      raster_preservation: { renderer: "libreoffice", approximate: true, reasons: ["svg_template_paint_unsupported", "svg_template_image_crop_unsupported"],
        shape_refs: ["ppt/slideLayouts/slideLayout4.xml#8", "ppt/slideLayouts/slideLayout4.xml#10"] } },
    { element_id: "photo", source_part: "ppt/slideLayouts/slideLayout4.xml", source_shape_id: "background", kind: "image", layer: "background", bounds,
      raster_preservation: { renderer: "libreoffice", approximate: true, reasons: ["svg_template_background_unsupported"],
        shape_refs: ["ppt/slideLayouts/slideLayout4.xml#background"] } },
    { element_id: "logo", source_part: "ppt/slideLayouts/slideLayout4.xml", source_shape_id: "16", kind: "image", layer: "foreground", bounds },
  ],
} as SvgTemplateFrame;

test("review selections name the source shapes, never the joined run id or the background", () => {
  assert.deepEqual((frame.elements ?? []).flatMap(frameElementShapeRefs), [
    "ppt/slideLayouts/slideLayout4.xml#8", "ppt/slideLayouts/slideLayout4.xml#10", "ppt/slideLayouts/slideLayout4.xml#16"]);
});

test("each decoration image is listed as not editable, with its reasons and renderer", () => {
  const lines = rasterPreservationLines(frame);
  assert.equal(lines.length, 2);
  assert.match(lines[0], /이미지로 보존\(장식\) · 원본 요소 2개 · 채우기, 그림 자르기 · LibreOffice 근사 렌더링 · PPTX에서 편집할 수 없음/);
  assert.match(lines[1], /이미지로 보존\(배경\).*배경/);
});

test("raster reasons get their own text instead of the source-mode message", () => {
  assert.match(templateReason("svg_template_raster_preservation_review_required"), /이미지로 보존/);
  assert.match(templateReason("svg_template_raster_renderer_unavailable"), /LibreOffice/);
  assert.match(templateReason("svg_template_fullslide_raster_forbidden"), /배경/);
  assert.doesNotMatch(templateReason("svg_template_raster_render_failed"), /원본 보존 방식/);
});
