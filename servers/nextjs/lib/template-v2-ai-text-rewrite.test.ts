import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  createTemplateV2AiRewriteWorkflow,
  createTemplateV2DeterministicRewriteProvider,
  type TemplateV2AiRewritePreviewInput,
  type TemplateV2AiRewriteProvider,
} from "./template-v2-ai-text-rewrite.ts";
import {
  renderTemplateV2GeneralSlideCanvasHtml,
} from "./template-v2-general-renderer.mjs";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "./template-v2-upstream-compat.ts";
import {
  EMPTY_TEMPLATE_V2_STUDIO_STATE,
  getSelectedElement,
  templateV2StudioReducer,
  type JsonRecord,
  type StudioSelection,
} from "./template-v2-studio.ts";

const pinnedUpstreamElements = JSON.parse(
  readFileSync(
    new URL(
      "../../fastapi/tests/fixtures/template_v2/upstream-elements-57b194b.json",
      import.meta.url
    ),
    "utf8"
  )
) as {
  upstream_sha: string;
  elements: JsonRecord[];
};

const fixtureTextElement = pinnedUpstreamElements.elements.find(
  (element) => element.type === "text"
);
assert.ok(fixtureTextElement);

const selection: StudioSelection = {
  layoutId: "upstream-ai-rewrite",
  componentId: "text-component",
  elementPath: [0],
};

function previewInput(
  element: JsonRecord,
  idempotencyKey: string
): TemplateV2AiRewritePreviewInput {
  return {
    targetId: "upstream-ai-rewrite/text-component/0",
    element,
    selection: { runIndex: 0, start: 0, end: 5 },
    action: { kind: "rewrite" },
    expectedRevision: 7,
    currentRevision: 7,
    idempotencyKey,
    candidateCount: 2,
  };
}

test("AI text rewrite is default-deny for absent or external providers", async () => {
  const noProvider = await createTemplateV2AiRewriteWorkflow().preview(
    previewInput(structuredClone(fixtureTextElement), "deny-none-1")
  );
  assert.deepEqual(noProvider, {
    ok: false,
    status: 503,
    code: "template_v2_ai_rewrite_provider_unavailable",
  });

  const externalProvider = {
    kind: "external-network",
    generate: async () => ["one", "two"],
  } as unknown as TemplateV2AiRewriteProvider;
  const external = await createTemplateV2AiRewriteWorkflow(
    externalProvider
  ).preview(
    previewInput(structuredClone(fixtureTextElement), "deny-external-1")
  );
  assert.deepEqual(external, noProvider);

  const spoofedProvider = {
    kind: "deterministic-fixture",
    generate: async () => ["one", "two"],
  } as TemplateV2AiRewriteProvider;
  const spoofed = await createTemplateV2AiRewriteWorkflow(
    spoofedProvider
  ).preview(
    previewInput(structuredClone(fixtureTextElement), "deny-spoofed-1")
  );
  assert.deepEqual(spoofed, noProvider);

  assert.throws(
    () =>
      createTemplateV2DeterministicRewriteProvider(
        (async () => ["one", "two"]) as never
      ),
    /fixed candidate array/
  );
});

test("AI rewrite actions with unproven fields fail closed", async () => {
  const workflow = createTemplateV2AiRewriteWorkflow(
    createTemplateV2DeterministicRewriteProvider(["one", "two"])
  );
  const input = previewInput(
    structuredClone(fixtureTextElement),
    "deny-action-extra-1"
  );
  input.action = { kind: "rewrite", futureOption: true } as never;

  assert.deepEqual(await workflow.preview(input), {
    ok: false,
    status: 400,
    code: "template_v2_ai_rewrite_invalid_action",
  });
});

test("rewrites pinned upstream text through Studio, JSON reload, and render", async () => {
  assert.equal(
    pinnedUpstreamElements.upstream_sha,
    "57b194b234b42c8b28f8a507a30322de200e3e83"
  );
  const source = {
    layouts: [
      {
        id: selection.layoutId,
        future_layout_field: "kept",
        components: [
          {
            id: selection.componentId,
            position: { x: 20, y: 20 },
            size: { width: 480, height: 100 },
            elements: [structuredClone(fixtureTextElement)],
          },
        ],
      },
    ],
    future_envelope_field: "kept",
    future_generation_info: {
      schema: "presenton.template-v2-generation-provenance/v1",
      generation_strategy: "template-v2",
      template_v2_revision: 7,
      future_generation_field: "kept",
    },
  };
  const compatibility = adaptUpstreamTemplateV2LayoutsToStudio(source);
  let studio = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts: compatibility.studioLayouts,
  });
  const selected = getSelectedElement(studio.layouts, selection);
  assert.ok(selected);

  const workflow = createTemplateV2AiRewriteWorkflow(
    createTemplateV2DeterministicRewriteProvider([
      "Executive summary",
      "Leadership summary",
    ])
  );
  const preview = await workflow.preview(
    previewInput(selected, "fixture-preview-1")
  );
  assert.equal(preview.ok, true);
  if (!preview.ok) return;
  const apply = workflow.apply({
    previewId: preview.preview.id,
    candidateId: preview.preview.candidates[0].id,
    element: selected,
    expectedRevision: 7,
    currentRevision: 7,
    idempotencyKey: "fixture-preview-1",
  });
  assert.equal(apply.ok, true);
  if (!apply.ok) return;
  assert.deepEqual(apply.autosave, {
    expected_revision: 7,
    idempotency_key: "fixture-preview-1",
  });

  studio = templateV2StudioReducer(studio, {
    type: "replace-element",
    selection,
    element: apply.element,
    historyKey: apply.historyKey,
  });
  assert.equal(studio.dirty, true);

  const wire = serializeStudioLayoutsForUpstream(
    compatibility,
    studio.layouts ?? {}
  );
  const jsonReload = JSON.parse(JSON.stringify(wire));
  const reentered = adaptUpstreamTemplateV2LayoutsToStudio(jsonReload);
  assert.deepEqual(
    serializeStudioLayoutsForUpstream(reentered),
    jsonReload
  );
  assert.deepEqual(jsonReload.future_generation_info, {
    schema: "presenton.template-v2-generation-provenance/v1",
    generation_strategy: "template-v2",
    template_v2_revision: 7,
    future_generation_field: "kept",
  });
  assert.equal(jsonReload.future_envelope_field, "kept");
  assert.equal(jsonReload.layouts[0].future_layout_field, "kept");

  const reloadedElement = getSelectedElement(
    reentered.studioLayouts,
    selection
  );
  assert.ok(reloadedElement);
  assert.equal(
    (reloadedElement.runs as JsonRecord[])[0].text,
    "Executive summary"
  );
  assert.deepEqual(
    (reloadedElement.runs as JsonRecord[])[0].upstream_run_extension,
    { locale: "ko-KR" }
  );
  assert.equal(
    reloadedElement.upstream_element_extension,
    "text-vNext"
  );
  assert.equal(
    Array.from(String((reloadedElement.runs as JsonRecord[])[0].text)).length <=
      Number(reloadedElement.max_length),
    true
  );
  assert.equal(
    (fixtureTextElement.runs as JsonRecord[])[0].text,
    "Title"
  );

  const reloadedLayout = (
    reentered.studioLayouts.layouts as JsonRecord[]
  )[0];
  const html = renderTemplateV2GeneralSlideCanvasHtml({
    id: selection.layoutId,
    ui: reloadedLayout,
  });
  assert.match(html, /Executive summary/);
  assert.doesNotMatch(html, />Title</);
});

test("marks over-budget candidates unapplyable and rejects apply", async () => {
  const workflow = createTemplateV2AiRewriteWorkflow(
    createTemplateV2DeterministicRewriteProvider([
      "x".repeat(81),
      "y".repeat(81),
    ])
  );
  const element = structuredClone(fixtureTextElement);
  const preview = await workflow.preview(
    previewInput(element, "fixture-overflow-1")
  );
  assert.equal(preview.ok, true);
  if (!preview.ok) return;
  assert.deepEqual(
    preview.preview.candidates.map((candidate) => ({
      applyable: candidate.applyable,
      reason: candidate.reason,
    })),
    [
      {
        applyable: false,
        reason: "template_v2_ai_rewrite_max_length_exceeded",
      },
      {
        applyable: false,
        reason: "template_v2_ai_rewrite_max_length_exceeded",
      },
    ]
  );
  assert.deepEqual(
    workflow.apply({
      previewId: preview.preview.id,
      candidateId: preview.preview.candidates[0].id,
      element,
      expectedRevision: 7,
      currentRevision: 7,
      idempotencyKey: "fixture-overflow-1",
    }),
    {
      ok: false,
      status: 409,
      code: "template_v2_ai_rewrite_candidate_overflow",
    }
  );
});

test("marks below-minimum candidates unapplyable and rejects apply", async () => {
  const workflow = createTemplateV2AiRewriteWorkflow(
    createTemplateV2DeterministicRewriteProvider(["x", "Longer"])
  );
  const element = structuredClone(fixtureTextElement);
  element.min_length = 5;
  const preview = await workflow.preview(
    previewInput(element, "fixture-underflow-1")
  );
  assert.equal(preview.ok, true);
  if (!preview.ok) return;
  assert.deepEqual(
    preview.preview.candidates.map((candidate) => ({
      applyable: candidate.applyable,
      reason: candidate.reason,
    })),
    [
      {
        applyable: false,
        reason: "template_v2_ai_rewrite_min_length_not_met",
      },
      {
        applyable: true,
        reason: "template_v2_ai_rewrite_text_fits",
      },
    ]
  );
  assert.deepEqual(
    workflow.apply({
      previewId: preview.preview.id,
      candidateId: preview.preview.candidates[0].id,
      element,
      expectedRevision: 7,
      currentRevision: 7,
      idempotencyKey: "fixture-underflow-1",
    }),
    {
      ok: false,
      status: 409,
      code: "template_v2_ai_rewrite_candidate_underflow",
    }
  );
});

test("rejects malformed sibling run text and font before aggregate budgeting", async () => {
  const workflow = createTemplateV2AiRewriteWorkflow(
    createTemplateV2DeterministicRewriteProvider(["one", "two"])
  );
  const invalidText = structuredClone(fixtureTextElement);
  (invalidText.runs as unknown[]).push({ text: 123 });
  assert.deepEqual(
    await workflow.preview(previewInput(invalidText, "invalid-run-text-1")),
    {
      ok: false,
      status: 422,
      code: "template_v2_ai_rewrite_invalid_run",
    }
  );

  const invalidFont = structuredClone(fixtureTextElement);
  (invalidFont.runs as unknown[]).push({ text: "Sibling", font: "Arial" });
  assert.deepEqual(
    await workflow.preview(previewInput(invalidFont, "invalid-run-font-1")),
    {
      ok: false,
      status: 422,
      code: "template_v2_ai_rewrite_invalid_font",
    }
  );
});

test("rejects apply when source text changed after preview", async () => {
  const workflow = createTemplateV2AiRewriteWorkflow(
    createTemplateV2DeterministicRewriteProvider([
      "Executive summary",
      "Leadership summary",
    ])
  );
  const element = structuredClone(fixtureTextElement);
  const preview = await workflow.preview(
    previewInput(element, "fixture-stale-1")
  );
  assert.equal(preview.ok, true);
  if (!preview.ok) return;

  const staleElement = structuredClone(element);
  (staleElement.runs as JsonRecord[])[0].text = "Changed elsewhere";
  assert.deepEqual(
    workflow.apply({
      previewId: preview.preview.id,
      candidateId: preview.preview.candidates[0].id,
      element: staleElement,
      expectedRevision: 7,
      currentRevision: 7,
      idempotencyKey: "fixture-stale-1",
    }),
    {
      ok: false,
      status: 409,
      code: "template_v2_ai_rewrite_source_changed",
    }
  );
});

test("validates current source and unknown fields before idempotent apply replay", async () => {
  const workflow = createTemplateV2AiRewriteWorkflow(
    createTemplateV2DeterministicRewriteProvider([
      "Executive summary",
      "Leadership summary",
    ])
  );
  const element = structuredClone(fixtureTextElement);
  const preview = await workflow.preview(
    previewInput(element, "fixture-apply-replay-1")
  );
  assert.equal(preview.ok, true);
  if (!preview.ok) return;
  const applyInput = {
    previewId: preview.preview.id,
    candidateId: preview.preview.candidates[0].id,
    element,
    expectedRevision: 7,
    currentRevision: 7,
    idempotencyKey: "fixture-apply-replay-1",
  };
  const first = workflow.apply(applyInput);
  assert.equal(first.ok, true);
  assert.deepEqual(workflow.apply(applyInput), first);

  const unknownFieldChanged = structuredClone(element);
  (unknownFieldChanged.runs as JsonRecord[])[0].upstream_run_extension = {
    locale: "en-US",
    concurrent: true,
  };
  assert.deepEqual(
    workflow.apply({ ...applyInput, element: unknownFieldChanged }),
    {
      ok: false,
      status: 409,
      code: "template_v2_ai_rewrite_source_changed",
    }
  );

  const sourceChanged = structuredClone(element);
  (sourceChanged.runs as JsonRecord[])[0].text = "Changed concurrently";
  assert.deepEqual(workflow.apply({ ...applyInput, element: sourceChanged }), {
    ok: false,
    status: 409,
    code: "template_v2_ai_rewrite_source_changed",
  });

  assert.deepEqual(workflow.apply({ ...applyInput, currentRevision: 8 }), {
    ok: false,
    status: 409,
    code: "template_v2_ai_rewrite_stale_revision",
  });
});
