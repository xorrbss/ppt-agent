import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  executePersistedPresentationExport,
  type PersistedPresentation,
  type PresentationExportExecutionParams,
  resolvePersistedExportStrategy,
} from "./presentation-export-strategy.ts";

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
