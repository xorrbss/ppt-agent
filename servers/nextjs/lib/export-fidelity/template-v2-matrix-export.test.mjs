import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

import { executeExportAtProductionBoundary } from "../presentation-export-boundary.ts";
import { PRESENTATION_DRIVER_REGISTRY } from "../presentation-profile.ts";
import { PRESENTATION_SOURCE_SHA256 } from "../presentation-export-strategy.ts";
import { RenderService, createRenderPresentationSnapshot } from "../render-service/index.ts";
import { renderTemplateV2GeneralPresentationHtml } from "../template-v2-general-renderer.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const NEXT_ROOT = path.resolve(HERE, "..", "..");
const FIXTURE_ROOT = path.join(HERE, "fixtures", "template-v2-general");

const matrix = [
  ["title-body", "00-title-body", "Quarterly direction"],
  ["geometry", "01-geometry", "Operating model"],
  ["text-fit", "02-text-fit", "VISIBLE SENTINEL"],
  ["rich-elements", "03-rich-elements", "Renderer contract"],
  ["upstream-text", "03-rich-elements", "Fast"],
  ["upstream-image", "03-rich-elements", "Grouped"],
  ["upstream-table", "03-rich-elements", "Revenue"],
  ["nested-lock-remap", "03-rich-elements", "Finance model"],
];

test("eight representative Template V2 fixtures cross the PPTX export strategy boundary", async () => {
  assert.equal(matrix.length, 8);
  for (const [id, directory, expectedText] of matrix) {
    const presentation = JSON.parse(
      await fs.readFile(path.join(FIXTURE_ROOT, directory, "template.v2.json"), "utf8")
    );
    const driver = PRESENTATION_DRIVER_REGISTRY["template-v2"];
    Object.assign(presentation, {
      id, revision: 1, lifecycle_status: "published",
      profile: {
        schema_version: 1, driver_id: driver.driver_id,
        content_model: driver.content_model, generation_strategy: driver.generation_strategy,
        style_source: "imported", generation_mode: "standard",
      },
      capabilities: {
        schema_version: 1, driver_id: driver.driver_id,
        renderer_adapter_id: driver.renderer_adapter_id,
        editor_adapter_id: driver.editor_adapter_id, editor: driver.editor,
        agent_tool_capability: driver.agent_tool_capability,
        export_adapter_id: driver.export_adapter_id,
        export_editable_pptx: driver.export_editable_pptx,
      },
    });
    // The exporter below is a test transport; supply its persisted chart
    // receipt metadata too, as the real production boundary now requires it.
    const preview = new RenderService().render({
      target: "preview", snapshot: createRenderPresentationSnapshot(presentation),
    });
    presentation.render_dependencies = {
      chart_snapshots: preview.chart_snapshot.chart_ids.map((chartId) => ({
        chart_id: chartId,
        slide_id: preview.slide_discovery.slide_ids[0],
        asset_id: `matrix-chart-${chartId}`, mime_type: "image/svg+xml",
        sha256: createHash("sha256").update(chartId).digest("hex"),
      })),
    };
    Object.defineProperty(presentation, PRESENTATION_SOURCE_SHA256, {
      value: createHash("sha256").update(JSON.stringify(presentation)).digest("hex"),
    });
    const result = await executeExportAtProductionBoundary(
      { format: "pptx", presentationId: id, cookieHeader: "matrix-cookie" },
      {
        async fetchPresentation(presentationId) {
          assert.equal(presentationId, id);
          return presentation;
        },
        registry: {
          async general(params) {
            const html = renderTemplateV2GeneralPresentationHtml(presentation);
            assert.match(html, /data-layout="template-v2-general"/);
            assert.ok(html.includes(expectedText), `${id} lost ${expectedText}`);
            return { path: `memory://${params.presentationId}.pptx` };
          },
          async hybrid() {
            assert.fail("Template V2 matrix must not use authored-hybrid export");
          },
        },
      }
    );
    assert.equal(result.path, `memory://${id}.pptx`);
  }
  // Keep the fixture paths resolvable from the Next.js workspace. This guards
  // against a matrix entry silently becoming a stale documentation-only name.
  await fs.access(path.join(NEXT_ROOT, "cypress", "fixtures", "template-v2-matrix", "templateV2MatrixFixtures.ts"));
});

