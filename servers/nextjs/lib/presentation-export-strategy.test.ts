import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  executePersistedPresentationExport,
  PRESENTATION_SOURCE_SHA256,
  type PersistedPresentation,
  type PresentationExportExecutionParams,
  resolvePersistedExportStrategy,
} from "./presentation-export-strategy.ts";
import {
  PRESENTATION_DRIVER_REGISTRY,
  type PresentationDriverId,
} from "./presentation-profile.ts";
import {
  createRenderPresentationSnapshot,
  RenderService,
} from "./render-service/index.ts";

interface StrategyParityCase {
  name: string;
  presentation: PersistedPresentation;
  expected: {
    strategy?: string;
    error?: string;
  };
}

const strategyParityContract = JSON.parse(
  readFileSync(
    new URL(
      "../../fastapi/tests/fixtures/template_v2/strategy-parity.json",
      import.meta.url
    ),
    "utf8"
  )
) as {
  contract: string;
  cases: StrategyParityCase[];
};

assert.equal(
  strategyParityContract.contract,
  "persisted-presentation-strategy-v1"
);

const pptxParams: PresentationExportExecutionParams = {
  format: "pptx",
  presentationId: "presentation-id",
  cookieHeader: "session=test",
};

function validTemplateV2(): PersistedPresentation {
  return {
    version: "v2-standard",
    mode: "template",
    slides: [{ ui: { description: "native" }, html_content: null }],
  };
}

function validAuthored(): PersistedPresentation {
  return {
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  };
}

function validLegacy(): PersistedPresentation {
  return {
    version: "v1-standard",
    mode: "template",
    slides: [{ ui: null, html_content: null }],
  };
}

function validLegacyAuthoredSentinel(): PersistedPresentation {
  return {
    version: "v1-standard",
    mode: null,
    slides: [
      {
        layout_group: "authored",
        content: { __authored__: true },
        ui: null,
        html_content: "<section>legacy editable</section>",
      },
    ],
  };
}

function withCanonicalRuntime(
  presentation: PersistedPresentation,
  driverId: PresentationDriverId,
): PersistedPresentation {
  const driver = PRESENTATION_DRIVER_REGISTRY[driverId];
  return {
    ...presentation,
    profile: {
      schema_version: 1,
      driver_id: driverId,
      content_model: driver.content_model,
      generation_strategy: driver.generation_strategy,
      style_source:
        driverId === "authored"
          ? "authored"
          : driverId === "template-v2"
            ? "imported"
            : "builtin",
      generation_mode: driverId === "smart" ? "smart" : "standard",
    },
    capabilities: {
      schema_version: 1,
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

function spyRegistry() {
  const calls = { general: 0, hybrid: 0 };
  return {
    calls,
    registry: {
      async general() {
        calls.general += 1;
        return { path: "general.pptx" };
      },
      async hybrid() {
        calls.hybrid += 1;
        return { path: "hybrid.pptx" };
      },
    },
  };
}

test("Template V2 delegates only to the existing general executor", async () => {
  const { calls, registry } = spyRegistry();
  const result = await executePersistedPresentationExport(
    validTemplateV2(),
    pptxParams,
    registry
  );

  assert.equal(result.path, "general.pptx");
  assert.deepEqual(calls, { general: 1, hybrid: 0 });
});

test("legacy template delegates only to the existing general executor", async () => {
  const { calls, registry } = spyRegistry();
  const result = await executePersistedPresentationExport(
    validLegacy(),
    { ...pptxParams, pptxMode: "hybrid" },
    registry
  );

  assert.equal(result.path, "general.pptx");
  assert.deepEqual(calls, { general: 1, hybrid: 0 });
});

test("authored HTML delegates only to the existing hybrid executor", async () => {
  const { calls, registry } = spyRegistry();
  const result = await executePersistedPresentationExport(
    validAuthored(),
    { ...pptxParams, pptxMode: "hybrid" },
    registry
  );

  assert.equal(result.path, "hybrid.pptx");
  assert.deepEqual(calls, { general: 0, hybrid: 1 });
});

test("legacy authored slide sentinel still delegates to hybrid", async () => {
  const { calls, registry } = spyRegistry();
  const result = await executePersistedPresentationExport(
    validLegacyAuthoredSentinel(),
    { ...pptxParams, pptxMode: "hybrid" },
    registry
  );

  assert.equal(result.path, "hybrid.pptx");
  assert.deepEqual(calls, { general: 0, hybrid: 1 });
});

for (const [label, pptxMode] of [
  ["missing", undefined],
  ["fidelity", "fidelity" as const],
] as const) {
  test(`authored PPTX ${label} mode preserves the general executor`, async () => {
    const { calls, registry } = spyRegistry();
    const result = await executePersistedPresentationExport(
      validAuthored(),
      { ...pptxParams, pptxMode },
      registry
    );

    assert.equal(result.path, "general.pptx");
    assert.deepEqual(calls, { general: 1, hybrid: 0 });
  });
}

test("Template V2 ignores a hybrid hint and never calls hybrid", async () => {
  const { calls, registry } = spyRegistry();
  const result = await executePersistedPresentationExport(
    validTemplateV2(),
    { ...pptxParams, pptxMode: "hybrid" },
    registry
  );

  assert.equal(result.path, "general.pptx");
  assert.deepEqual(calls, { general: 1, hybrid: 0 });
});

test("PDF keeps the general executor after authored identity validation", async () => {
  const { calls, registry } = spyRegistry();
  await executePersistedPresentationExport(
    validAuthored(),
    { ...pptxParams, format: "pdf" },
    registry
  );

  assert.deepEqual(calls, { general: 1, hybrid: 0 });
});

for (const fixtureCase of strategyParityContract.cases) {
  test(`shared strategy contract: ${fixtureCase.name}`, () => {
    if (fixtureCase.expected.error) {
      assert.throws(
        () => resolvePersistedExportStrategy(fixtureCase.presentation),
        new RegExp(fixtureCase.expected.error)
      );
      return;
    }

    assert.equal(
      resolvePersistedExportStrategy(fixtureCase.presentation),
      fixtureCase.expected.strategy
    );
  });
}

test("invalid identities fail closed before either executor", async () => {
  const invalidPresentations: PersistedPresentation[] = [
    { ...validTemplateV2(), mode: "adaptive" },
    { ...validTemplateV2(), mode: "bogus" },
    {
      ...validTemplateV2(),
      theme: { mode: "authored" },
    },
    {
      ...validTemplateV2(),
      theme: { mode: "adaptive" },
    },
    {
      ...validTemplateV2(),
      theme: { mode: "bogus" },
    },
    {
      version: "v1-standard",
      mode: "authored",
      slides: [{ ui: { native: true }, html_content: null }],
    },
    {
      version: "v9",
      mode: "template",
      slides: [{ ui: null, html_content: null }],
    },
  ];

  for (const presentation of invalidPresentations) {
    const { calls, registry } = spyRegistry();
    await assert.rejects(
      executePersistedPresentationExport(
        presentation,
        pptxParams,
        registry
      )
    );
    assert.deepEqual(calls, { general: 0, hybrid: 0 });
  }
});

test("server-issued profiles select the same export adapter and reject spoofed conflicts", () => {
  assert.equal(
    resolvePersistedExportStrategy(
      withCanonicalRuntime(validTemplateV2(), "template-v2"),
    ),
    "template-v2-general",
  );
  assert.equal(
    resolvePersistedExportStrategy(
      withCanonicalRuntime(validAuthored(), "authored"),
    ),
    "authored-hybrid",
  );
  assert.throws(
    () =>
      resolvePersistedExportStrategy(
        withCanonicalRuntime(validTemplateV2(), "standard"),
      ),
    /presentation_profile_export_identity_conflict/,
  );
});

test("export revision freshness fails before a runner is invoked", async () => {
  const { calls, registry } = spyRegistry();
  await assert.rejects(
    executePersistedPresentationExport(
      { ...validLegacy(), revision: 4 },
      { ...pptxParams, expectedPresentationRevision: 3 },
      registry,
    ),
    /stale_render_revision/,
  );
  assert.deepEqual(calls, { general: 0, hybrid: 0 });
});

test("canonical export artifacts are checked against the fetched revision and digest", async () => {
  const source = {
    ...withCanonicalRuntime(validLegacy(), "standard"),
    id: "deck-standard",
    revision: 4,
    lifecycle_status: "published",
    source_sha256: "c".repeat(64),
    slides: [{ id: "slide-1", ui: null, html_content: null }],
  };
  Object.defineProperty(source, PRESENTATION_SOURCE_SHA256, {
    enumerable: false,
    value: source.source_sha256,
  });
  const artifact = new RenderService().render({
    target: "pptx",
    snapshot: createRenderPresentationSnapshot(source),
  });
  const accepted = await executePersistedPresentationExport(
    source,
    { ...pptxParams, expectedPresentationRevision: 4 },
    {
      async general() {
        return { path: "canonical.pptx", renderArtifact: artifact };
      },
      async hybrid() {
        throw new Error("unexpected_hybrid");
      },
    },
  );
  assert.equal(accepted.renderArtifact?.artifact_version, artifact.artifact_version);

  const stale = {
    ...structuredClone(artifact),
    source: { ...artifact.source, revision: 3 },
  };
  await assert.rejects(
    executePersistedPresentationExport(
      source,
      { ...pptxParams, expectedPresentationRevision: 4 },
      {
        async general() {
          return { path: "stale.pptx", renderArtifact: stale };
        },
        async hybrid() {
          throw new Error("unexpected_hybrid");
        },
      },
    ),
    /stale_render_artifact/,
  );
});
