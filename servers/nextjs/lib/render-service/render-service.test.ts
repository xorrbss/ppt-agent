import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  PRESENTATION_DRIVER_REGISTRY,
  type PresentationDriverId,
  type PresentationProfile,
} from "../presentation-profile.ts";
import {
  compileTemplateRenderPlan,
  type CanonicalTemplateAst,
} from "../template-v2-ast/index.ts";
import {
  assertRenderArtifactFresh,
  createRenderPresentationSnapshot,
  RenderPolicyError,
  RenderService,
} from "./index.ts";

const SHA = "a".repeat(64);

function runtime(driverId: PresentationDriverId) {
  const driver = PRESENTATION_DRIVER_REGISTRY[driverId];
  const styleSource =
    driverId === "authored"
      ? "authored"
      : driverId === "template-v2"
        ? "imported"
        : "builtin";
  const profile: PresentationProfile = {
    schema_version: 1,
    driver_id: driver.driver_id,
    content_model: driver.content_model,
    generation_strategy: driver.generation_strategy,
    style_source: styleSource,
    generation_mode: driverId === "smart" ? "smart" : "standard",
  };
  return {
    profile,
    capabilities: {
      schema_version: 1 as const,
      driver_id: driver.driver_id,
      renderer_adapter_id: driver.renderer_adapter_id,
      editor_adapter_id: driver.editor_adapter_id,
      editor: driver.editor,
      agent_tool_capability: driver.agent_tool_capability,
      export_adapter_id: driver.export_adapter_id,
      export_editable_pptx: driver.export_editable_pptx,
    },
  };
}

function presentation(
  driverId: PresentationDriverId,
  slides: Array<Record<string, unknown>> = [
    { id: "slide-1", elements: [{ id: "title", type: "text" }] },
  ],
) {
  return {
    id: `deck-${driverId}`,
    revision: 7,
    lifecycle_status: "published",
    source_sha256: SHA,
    slides,
    ...runtime(driverId),
  };
}

test("all default drivers produce the same revision-bound artifact contract", () => {
  const service = new RenderService();
  for (const driverId of ["standard", "smart", "authored"] as const) {
    const snapshot = createRenderPresentationSnapshot(presentation(driverId));
    const artifact = service.render({ target: "preview", snapshot });
    assert.equal(artifact.schema_version, "presenton.render-artifact/v1");
    assert.equal(artifact.driver_id, driverId);
    assert.equal(artifact.source.revision, 7);
    assert.equal(artifact.source.published, true);
    assert.match(artifact.artifact_version, /revision-7\/published/);
    assert.equal(artifact.slide_discovery.count, 1);
    assert.deepEqual(artifact.selection_map[0]?.object_ids, ["title"]);
  }
});

test("preview, thumbnail, selection map, PDF, and PPTX share one artifact shape", () => {
  const snapshot = createRenderPresentationSnapshot(
    presentation("standard", [
      { id: "slide-1", elements: [{ id: "title", type: "text" }] },
      { id: "slide-2", elements: [{ id: "image", type: "image" }] },
    ]),
  );
  const service = new RenderService();
  const artifacts = [
    "preview",
    "thumbnail",
    "selection-map",
    "pdf",
    "pptx",
  ].map((target) =>
    service.render({
      target: target as "preview" | "thumbnail" | "selection-map" | "pdf" | "pptx",
      snapshot,
      fonts: [{ family: "Inter", status: "ready" }],
      assets: [{ asset_id: "image", kind: "image", status: "ready" }],
    }),
  );
  for (const artifact of artifacts) {
    assert.equal(artifact.schema_version, "presenton.render-artifact/v1");
    assert.equal(artifact.renderer_version, "legacy-elements@1");
    assert.equal(artifact.slide_discovery.count, 2);
    assert.equal(artifact.fonts[0]?.family, "Inter");
    assert.equal(artifact.assets[0]?.asset_id, "image");
    assert.deepEqual(
      artifact.selection_map.map((entry) => entry.slide_id),
      ["slide-1", "slide-2"],
    );
  }
});

test("smart charts use controlled preview runtime and require immutable snapshots for export", () => {
  const snapshot = createRenderPresentationSnapshot(
    presentation("smart", [
      { id: "slide-1", elements: [{ id: "revenue", type: "chart" }] },
    ]),
  );
  const service = new RenderService();
  const preview = service.render({ target: "preview", snapshot });
  assert.equal(preview.chart_snapshot.status, "runtime-required");
  assert.equal(preview.script_policy.mode, "controlled-runtime");

  assert.throws(
    () => service.render({ target: "pdf", snapshot }),
    /render_chart_snapshot_required:revenue/,
  );
  const pdf = service.render({
    target: "pdf",
    snapshot,
    chart_snapshots: [
      {
        chart_id: "revenue",
        slide_id: "slide-1",
        mime_type: "image/png",
        sha256: "b".repeat(64),
        asset_id: "asset-chart-revenue",
      },
    ],
  });
  assert.equal(pdf.chart_snapshot.status, "complete");
  assert.equal(pdf.script_policy.mode, "deny");
  assert.equal(pdf.script_policy.iframe_free, true);
});

test("Template V2 adapter consumes only the shared canonical render plan", () => {
  const ast = JSON.parse(
    readFileSync(
      new URL(
        "../../../../contracts/template_v2/fixtures/repeatable-grouped-content.a3352f3c.ast.json",
        import.meta.url,
      ),
      "utf8",
    ),
  ) as CanonicalTemplateAst;
  const plan = compileTemplateRenderPlan(ast, "canonical_repeat_fixture", {
    regular_cards: { cards: [{ card_title: "A" }, { card_title: "B" }] },
    positioned_callouts: {
      items: [{ item_title: "One" }, { item_title: "Two" }],
    },
  });
  const snapshot = createRenderPresentationSnapshot(
    presentation("template-v2", [{ id: "slide-1", ui: { persisted: true } }]),
  );
  const service = new RenderService();

  assert.throws(
    () => service.render({ target: "preview", snapshot }),
    /render_template_v2_plan_required/,
  );
  const artifact = service.render({
    target: "preview",
    snapshot,
    template_render_plans: [plan],
  });
  assert.equal(artifact.renderer_version, "template-v2-canonical@1");
  const payload = artifact.payload as { slides: Array<Record<string, unknown>> };
  assert.equal(payload.slides.length, 1);
});

test("unknown profiles, stale revisions, stale artifacts, overflow, and unsafe HTML fail closed", () => {
  const service = new RenderService();
  const unknown = presentation("standard") as Record<string, unknown>;
  unknown.profile = { ...(unknown.profile as object), driver_id: "future" };
  assert.throws(
    () => createRenderPresentationSnapshot(unknown),
    /unsupported_presentation_driver/,
  );

  const snapshot = createRenderPresentationSnapshot(presentation("standard"));
  assert.throws(
    () => service.render({ target: "preview", snapshot, expected_revision: 6 }),
    /stale_render_revision/,
  );
  const artifact = service.render({ target: "preview", snapshot });
  assert.throws(
    () =>
      assertRenderArtifactFresh(artifact, {
        ...snapshot.source,
        revision: 8,
      }),
    /stale_render_artifact/,
  );

  const overflow = createRenderPresentationSnapshot(
    presentation("standard", [
      { id: "slide-1", elements: [{ id: "body", overflow: true }] },
    ]),
  );
  assert.throws(
    () => service.render({ target: "pptx", snapshot: overflow }),
    /render_overflow_validation_failed/,
  );

  const iframe = createRenderPresentationSnapshot(
    presentation("authored", [
      { id: "slide-1", html_content: "<iframe src='https://example.test'>" },
    ]),
  );
  assert.throws(
    () => service.render({ target: "pdf", snapshot: iframe }),
    /render_iframe_forbidden/,
  );
});

test("unready assets never cross the renderer boundary", () => {
  const snapshot = createRenderPresentationSnapshot(presentation("standard"));
  assert.throws(
    () =>
      new RenderService().render({
        target: "preview",
        snapshot,
        assets: [
          {
            asset_id: "asset-1",
            kind: "image",
            status: "scanning",
          } as never,
        ],
      }),
    (error) =>
      error instanceof RenderPolicyError && error.code === "render_asset_not_ready",
  );
});
