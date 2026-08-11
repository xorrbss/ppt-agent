import assert from "node:assert/strict";
import test from "node:test";

import { PRESENTATION_DRIVER_REGISTRY } from "../../../../lib/presentation-profile.ts";
import { buildPresentationFeatureViewModel } from "./presentationFeatureSlots.ts";
import { PRESENTATION_PAGE_DRIVER_ADAPTER_REGISTRY } from "./presentationFeatureSlots.ts";

function runtime(driverId: keyof typeof PRESENTATION_DRIVER_REGISTRY) {
  const driver = PRESENTATION_DRIVER_REGISTRY[driverId];
  return {
    profile: {
      schema_version: 1 as const,
      driver_id: driverId,
      content_model: driver.content_model,
      generation_strategy: driver.generation_strategy,
      style_source: driver.style_sources[0],
      generation_mode: driverId === "smart" ? ("smart" as const) : ("standard" as const),
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
    driver,
  };
}

test("feature slots are driven by the validated presentation capability", () => {
  const smart = buildPresentationFeatureViewModel({
    runtime: runtime("smart"),
    workspaceReady: true,
  });
  const authored = buildPresentationFeatureViewModel({
    runtime: runtime("authored"),
    workspaceReady: true,
  });

  assert.equal(smart.slots.selectionOverlay, true);
  assert.equal(authored.slots.selectionOverlay, false);
  assert.equal(authored.authored, true);
  assert.equal(authored.slots.editorSurface, true);
  assert.equal(authored.slots.assistantPanel, true);
  assert.deepEqual(authored.runtimeDependencies, [
    "authored",
    "authored-readonly",
    "authored-hybrid",
  ]);
  assert.deepEqual(authored.renderActions, ["preview", "pdf", "pptx"]);
  assert.equal(smart.background, "#F6F6F9");
  assert.equal(smart.componentAdapters.editor, "smart-html");
  assert.equal(smart.componentAdapters.assistant, "canonical-workspace");
  assert.equal(smart.componentAdapters.selection, "smart-html-selection");
  assert.equal(authored.componentAdapters.editor, "authored-readonly");
  assert.equal(authored.componentAdapters.export, "authored-hybrid");
});

test("every presentation driver supplies the full page composition", () => {
  assert.deepEqual(
    Object.keys(PRESENTATION_PAGE_DRIVER_ADAPTER_REGISTRY).sort(),
    Object.keys(PRESENTATION_DRIVER_REGISTRY).sort(),
  );
  for (const [driverId, adapter] of Object.entries(
    PRESENTATION_PAGE_DRIVER_ADAPTER_REGISTRY,
  )) {
    const driver = PRESENTATION_DRIVER_REGISTRY[
      driverId as keyof typeof PRESENTATION_DRIVER_REGISTRY
    ];
    assert.equal(adapter.driverId, driverId);
    assert.equal(adapter.editorAdapterId, driver.editor_adapter_id);
    assert.equal(adapter.exportAdapterId, driver.export_adapter_id);
    assert.equal(adapter.assistantAdapterId, "canonical-workspace");
    assert.deepEqual(adapter.renderActions, ["preview", "pdf", "pptx"]);
  }
});

test("unknown profiles fail closed while loading retains only the skeleton slot", () => {
  const unknown = buildPresentationFeatureViewModel({ runtime: null, workspaceReady: true });
  assert.equal(unknown.reviewRequired, true);
  assert.equal(unknown.slots.editorSurface, false);
  assert.equal(unknown.slots.headerActions, false);
  assert.equal(unknown.slots.exportActions, false);
  assert.deepEqual(unknown.renderActions, []);
  assert.equal(unknown.componentAdapters.editor, null);
  assert.equal(unknown.componentAdapters.assistant, null);
  assert.equal(unknown.selectionEnabled, false);

  const loading = buildPresentationFeatureViewModel({
    runtime: null,
    workspaceReady: false,
    loading: true,
  });
  assert.equal(loading.reviewRequired, false);
  assert.equal(loading.slots.editorSurface, true);
  assert.equal(loading.slots.assistantPanel, false);
});
