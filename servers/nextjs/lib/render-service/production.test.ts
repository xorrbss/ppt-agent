import assert from "node:assert/strict";
import test from "node:test";

import {
  PRESENTATION_DRIVER_REGISTRY,
  type PresentationDriverId,
} from "../presentation-profile.ts";
import {
  createProductionRenderArtifact,
  renderArtifactSlides,
} from "./production.ts";

const SOURCE_SHA256 = "e".repeat(64);

function runtime(driverId: PresentationDriverId) {
  const driver = PRESENTATION_DRIVER_REGISTRY[driverId];
  return {
    profile: {
      schema_version: 1 as const,
      driver_id: driverId,
      content_model: driver.content_model,
      generation_strategy: driver.generation_strategy,
      style_source:
        driverId === "template-v2"
          ? ("imported" as const)
          : driverId === "authored"
            ? ("authored" as const)
            : ("builtin" as const),
      generation_mode: "standard" as const,
    },
    capabilities: {
      schema_version: 1 as const,
      driver_id: driverId,
      renderer_adapter_id: driver.renderer_adapter_id,
      editor_adapter_id: driver.editor_adapter_id,
      editor: driver.editor,
      agent_tool_capability: driver.agent_tool_capability,
      export_adapter_id: driver.export_adapter_id,
      export_editable_pptx: driver.export_editable_pptx,
    },
  };
}

function persistedPresentation(
  driverId: PresentationDriverId,
  slides: readonly Record<string, unknown>[],
) {
  return {
    id: `production-${driverId}`,
    revision: 11,
    lifecycle_status: "published",
    fonts: { Inter: "/fonts/inter.woff2" },
    slides,
    ...runtime(driverId),
  };
}

const legacySlide = {
  id: "legacy-slide",
  ui: null,
  html_content: null,
  elements: [{ id: "legacy-title", type: "text", content: "Legacy" }],
};

const authoredSlide = {
  id: "authored-slide",
  ui: null,
  html_content: "<section><h1>Authored</h1></section>",
};

const templateV2Slide = {
  id: "template-slide",
  html_content: null,
  ui: {
    components: [
      {
        id: "body",
        position: { x: 0, y: 0 },
        elements: [
          {
            type: "text",
            name: "template-title",
            position: { x: 40, y: 40 },
            size: { width: 600, height: 80 },
            runs: [{ text: "Template V2" }],
          },
        ],
      },
    ],
  },
};

for (const regression of [
  {
    name: "template-v2-general",
    driverId: "template-v2" as const,
    slide: templateV2Slide,
    renderer: "template-v2-canonical@1",
  },
  {
    name: "authored-hybrid",
    driverId: "authored" as const,
    slide: authoredSlide,
    renderer: "authored-browser@1",
  },
  {
    name: "legacy-general",
    driverId: "standard" as const,
    slide: legacySlide,
    renderer: "legacy-elements@1",
  },
]) {
  test(`${regression.name} uses one production artifact for preview/PDF/PPTX`, () => {
    const presentation = persistedPresentation(regression.driverId, [
      regression.slide,
    ]);
    for (const target of ["preview", "pdf", "pptx"] as const) {
      const artifact = createProductionRenderArtifact(presentation, {
        target,
        source_sha256: SOURCE_SHA256,
        expected_revision: 11,
      });
      assert.equal(artifact.target, target);
      assert.equal(artifact.driver_id, regression.driverId);
      assert.equal(artifact.renderer_version, regression.renderer);
      assert.equal(artifact.source.revision, 11);
      assert.equal(artifact.source.source_sha256, SOURCE_SHA256);
      assert.match(artifact.artifact_version, /\/revision-11\/published\//);
      assert.equal(artifact.overflow_validation.status, "passed");
      assert.equal(artifact.fonts[0]?.family, "Inter");
      assert.equal(renderArtifactSlides(artifact)[0]?.id, regression.slide.id);
    }
  });
}

test("production export rejects stale revision, invalid dependency state, and overflow", () => {
  assert.throws(
    () =>
      createProductionRenderArtifact(
        persistedPresentation("standard", [legacySlide]),
        {
          target: "pptx",
          source_sha256: SOURCE_SHA256,
          expected_revision: 10,
        },
      ),
    /stale_render_revision/,
  );

  assert.throws(
    () =>
      createProductionRenderArtifact(
        persistedPresentation("standard", [
          {
            ...legacySlide,
            elements: [
              {
                id: "pending-image",
                type: "image",
                asset_id: "asset-1",
                status: "scanning",
              },
            ],
          },
        ]),
        {
          target: "pdf",
          source_sha256: SOURCE_SHA256,
        },
      ),
    /render_asset_not_ready/,
  );

  assert.throws(
    () =>
      createProductionRenderArtifact(
        persistedPresentation("standard", [
          {
            ...legacySlide,
            elements: [
              {
                id: "ready-image",
                type: "image",
                asset_id: "asset-1",
                status: "ready",
              },
            ],
          },
        ]),
        {
          target: "pdf",
          source_sha256: SOURCE_SHA256,
        },
      ),
    /render_dependency_digest_required/,
  );

  assert.throws(
    () =>
      createProductionRenderArtifact(
        persistedPresentation("standard", [
          {
            ...legacySlide,
            elements: [{ id: "overflow", type: "text", overflow: true }],
          },
        ]),
        {
          target: "pdf",
          source_sha256: SOURCE_SHA256,
        },
      ),
    /render_overflow_validation_failed/,
  );
});
