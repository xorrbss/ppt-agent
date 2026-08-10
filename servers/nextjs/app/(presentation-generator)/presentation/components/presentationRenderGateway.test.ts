import assert from "node:assert/strict";
import test from "node:test";

import { PRESENTATION_DRIVER_REGISTRY } from "../../../../lib/presentation-profile.ts";
import { createPresentationRenderActionFactory } from "./presentationRenderGateway.ts";

const driver = PRESENTATION_DRIVER_REGISTRY.standard;
const presentation = {
  id: "slot-deck",
  revision: 4,
  lifecycle_status: "published",
  source_sha256: "a".repeat(64),
  slides: [{ id: "slide-1", elements: [{ id: "title", type: "text" }] }],
  profile: {
    schema_version: 1 as const,
    driver_id: "standard" as const,
    content_model: driver.content_model,
    generation_strategy: driver.generation_strategy,
    style_source: "builtin" as const,
    generation_mode: "standard" as const,
  },
  capabilities: {
    schema_version: 1 as const,
    driver_id: "standard" as const,
    renderer_adapter_id: driver.renderer_adapter_id,
    editor_adapter_id: driver.editor_adapter_id,
    editor: driver.editor,
    agent_tool_capability: driver.agent_tool_capability,
    export_adapter_id: driver.export_adapter_id,
    export_editable_pptx: driver.export_editable_pptx,
  },
};

test("preview and export slot actions use revision-bound RenderArtifacts", () => {
  const actions = createPresentationRenderActionFactory();
  for (const action of ["preview", "pdf", "pptx"] as const) {
    const artifact = actions.render(action, presentation);
    assert.equal(artifact.target, action);
    assert.equal(artifact.schema_version, "presenton.render-artifact/v1");
    assert.equal(artifact.source.revision, 4);
  }
});
