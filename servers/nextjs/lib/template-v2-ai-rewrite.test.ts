import assert from "node:assert/strict";
import test from "node:test";

import {
  applyTemplateV2TextSelectionPatch,
  createTemplateV2AiRewriteWorkflow,
  createTemplateV2FakeAiRewriteProvider,
  diffTemplateV2AiRewrite,
  preflightTemplateV2TextFit,
  type JsonRecord,
} from "./template-v2-ai-rewrite.ts";
import {
  EMPTY_TEMPLATE_V2_STUDIO_STATE,
  getSelectedElement,
  templateV2StudioReducer,
  type StudioSelection,
} from "./template-v2-studio.ts";

function textElement(overrides: JsonRecord = {}): JsonRecord {
  return {
    type: "text",
    name: "title",
    position: { x: 20, y: 20 },
    size: { width: 400, height: 80 },
    max_length: 200,
    future_element_field: { retained: true },
    runs: [
      {
        text: "매출은 빠르게 증가했습니다.",
        font: { size: 20, bold: true },
        language: "ko-KR",
        future_run_field: { retained: true },
      },
    ],
    ...overrides,
  };
}

test("patches only a grapheme-bounded run and preserves unknown metadata", () => {
  const family = "👨‍👩‍👧‍👦";
  const source = `A${family}B`;
  const element = textElement({
    custom: { keep: "element" },
    runs: [
      {
        text: source,
        font: { size: 18, color: "#123456" },
        custom_run: { keep: true },
      },
    ],
  });
  const start = source.indexOf(family);
  const result = applyTemplateV2TextSelectionPatch(element, {
    runIndex: 0,
    start,
    end: start + family.length,
    expectedRunText: source,
    replacement: "팀",
  });

  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.deepEqual(result.element["custom"], { keep: "element" });
  assert.deepEqual(result.element, {
    ...element,
    runs: [
      {
        text: "A팀B",
        font: { size: 18, color: "#123456" },
        custom_run: { keep: true },
      },
    ],
  });
  assert.equal(result.selectedText, family);

  const splitSurrogate = applyTemplateV2TextSelectionPatch(element, {
    runIndex: 0,
    start: start + 1,
    end: start + family.length,
    expectedRunText: source,
    replacement: "invalid",
  });
  assert.deepEqual(splitSurrogate, {
    ok: false,
    element,
    reason: "template_v2_ai_rewrite_not_grapheme_boundary",
  });
});

test("merges adjacent runs only when all style and unknown metadata match", () => {
  const element = textElement({
    runs: [
      { text: "Hello ", font: { size: 18 }, extension: { locale: "en" } },
      { text: "world", font: { size: 18 }, extension: { locale: "en" } },
      { text: "!", font: { size: 18 }, extension: { locale: "ko" } },
    ],
  });
  const result = applyTemplateV2TextSelectionPatch(element, {
    runIndex: 1,
    start: 0,
    end: 5,
    expectedRunText: "world",
    replacement: "team",
  });

  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.deepEqual(result.element.runs, [
    { text: "Hello team", font: { size: 18 }, extension: { locale: "en" } },
    { text: "!", font: { size: 18 }, extension: { locale: "ko" } },
  ]);
});

test("a stale full-run guard makes replay and concurrent source changes no-ops", () => {
  const element = textElement();
  const patch = {
    runIndex: 0,
    start: 0,
    end: 2,
    expectedRunText: "an older run",
    replacement: "변경",
  };
  const result = applyTemplateV2TextSelectionPatch(element, patch);

  assert.deepEqual(result, {
    ok: false,
    element,
    reason: "template_v2_ai_rewrite_source_changed",
  });
});

test("text-fit preflight is deterministic and fails closed without geometry", () => {
  assert.deepEqual(preflightTemplateV2TextFit(textElement()).status, "fits");

  const overflowing = preflightTemplateV2TextFit(
    textElement({
      size: { width: 20, height: 20 },
      max_length: 1_000,
      runs: [{ text: "아주 긴 문장이 제한된 프레임을 넘습니다.", font: { size: 20 } }],
    }),
  );
  assert.equal(overflowing.status, "overflow");
  assert.equal(overflowing.reason, "template_v2_ai_rewrite_text_overflow");
  assert.ok(
    (overflowing.estimatedLines ?? 0) > (overflowing.availableLines ?? 0),
  );

  const unavailable = preflightTemplateV2TextFit(
    textElement({ size: undefined }),
  );
  assert.deepEqual(unavailable, {
    status: "unavailable",
    reason: "template_v2_ai_rewrite_missing_text_geometry",
    estimatedLines: null,
    availableLines: null,
  });
});

test("before/after diff respects grapheme clusters", () => {
  assert.deepEqual(diffTemplateV2AiRewrite("A👍🏽B", "A좋음B"), {
    before: "A👍🏽B",
    after: "A좋음B",
    unchangedPrefix: "A",
    removed: "👍🏽",
    inserted: "좋음",
    unchangedSuffix: "B",
  });
});

test("provider boundary is default-deny and stale revisions return 409", async () => {
  const unavailable = createTemplateV2AiRewriteWorkflow();
  const base = {
    targetId: "layout-1/component-1/element-0",
    element: textElement(),
    selection: { runIndex: 0, start: 0, end: 2 },
    action: { kind: "shorten" } as const,
    expectedRevision: 7,
    currentRevision: 7,
    idempotencyKey: "preview-request-1",
  };
  assert.deepEqual(await unavailable.preview(base), {
    ok: false,
    status: 503,
    code: "template_v2_ai_rewrite_provider_unavailable",
  });
  assert.deepEqual(
    await unavailable.preview({ ...base, currentRevision: 8 }),
    {
      ok: false,
      status: 409,
      code: "template_v2_ai_rewrite_stale_revision",
    },
  );
});

test("fake provider receives selected text only and supports preview/apply idempotency", async () => {
  const requests: unknown[] = [];
  const provider = createTemplateV2FakeAiRewriteProvider((request) => {
    requests.push(request);
    return ["매출 증가", "매출 급증", "성장 매출"];
  });
  const workflow = createTemplateV2AiRewriteWorkflow(provider);
  const element = textElement();
  const previewResult = await workflow.preview({
    targetId: "layout-1/component-1/element-0",
    element,
    selection: { runIndex: 0, start: 0, end: 2 },
    action: { kind: "tone-report" },
    expectedRevision: 7,
    currentRevision: 7,
    idempotencyKey: "preview-request-2",
  });

  assert.equal(previewResult.ok, true);
  if (!previewResult.ok) return;
  assert.deepEqual(requests, [
    {
      action: { kind: "tone-report" },
      selectedText: "매출",
      candidateCount: 3,
    },
  ]);
  assert.equal(previewResult.preview.candidates.length, 3);
  assert.equal(previewResult.preview.candidates[0].applyable, true);
  assert.deepEqual(previewResult.preview.candidates[0].diff, {
    before: "매출",
    after: "매출 증가",
    unchangedPrefix: "매출",
    removed: "",
    inserted: " 증가",
    unchangedSuffix: "",
  });

  const repeatedPreview = await workflow.preview({
    targetId: "layout-1/component-1/element-0",
    element,
    selection: { runIndex: 0, start: 0, end: 2 },
    action: { kind: "tone-report" },
    expectedRevision: 7,
    currentRevision: 7,
    idempotencyKey: "preview-request-2",
  });
  assert.equal(repeatedPreview, previewResult);
  assert.equal(requests.length, 1);

  const candidate = previewResult.preview.candidates[0];
  const applyInput = {
    previewId: previewResult.preview.id,
    candidateId: candidate.id,
    element,
    expectedRevision: 7,
    currentRevision: 7,
    idempotencyKey: "preview-request-2",
  };
  const applied = workflow.apply(applyInput);
  assert.equal(applied.ok, true);
  if (!applied.ok) return;
  assert.deepEqual(applied.autosave, {
    expected_revision: 7,
    idempotency_key: "preview-request-2",
  });
  assert.match(applied.historyKey, /^ai-rewrite:rewrite-/);
  assert.equal(
    ((applied.element.runs as JsonRecord[])[0]).text,
    "매출 증가은 빠르게 증가했습니다.",
  );
  assert.equal(workflow.apply(applyInput), applied);

  assert.deepEqual(
    workflow.apply({
      ...applyInput,
      candidateId: previewResult.preview.candidates[1].id,
    }),
    {
      ok: false,
      status: 409,
      code: "template_v2_ai_rewrite_idempotency_conflict",
    },
  );
});

test("overflow candidates remain previewable but cannot be applied", async () => {
  const workflow = createTemplateV2AiRewriteWorkflow(
    createTemplateV2FakeAiRewriteProvider(() => [
      "아주 긴 첫 번째 후보",
      "아주 긴 두 번째 후보",
    ]),
  );
  const element = textElement({
    size: { width: 20, height: 20 },
    max_length: 1_000,
  });
  const result = await workflow.preview({
    targetId: "element-1",
    element,
    selection: { runIndex: 0, start: 0, end: 2 },
    action: { kind: "expand" },
    candidateCount: 2,
    expectedRevision: 3,
    currentRevision: 3,
    idempotencyKey: "overflow-preview-1",
  });

  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.preview.candidates.length, 2);
  assert.equal(result.preview.candidates[0].applyable, false);
  assert.deepEqual(
    workflow.apply({
      previewId: result.preview.id,
      candidateId: result.preview.candidates[0].id,
      element,
      expectedRevision: 3,
      currentRevision: 3,
      idempotencyKey: "overflow-preview-1",
    }),
    {
      ok: false,
      status: 409,
      code: "template_v2_ai_rewrite_candidate_overflow",
    },
  );
});

test("cancel closes a preview without mutating the element", async () => {
  const element = textElement();
  const workflow = createTemplateV2AiRewriteWorkflow(
    createTemplateV2FakeAiRewriteProvider(() => ["요약", "매출 요약"]),
  );
  const result = await workflow.preview({
    targetId: "element-1",
    element,
    selection: { runIndex: 0, start: 0, end: 2 },
    action: { kind: "shorten" },
    candidateCount: 2,
    expectedRevision: 2,
    currentRevision: 2,
    idempotencyKey: "cancel-preview-1",
  });
  assert.equal(result.ok, true);
  if (!result.ok) return;

  assert.deepEqual(
    workflow.cancel({
      previewId: result.preview.id,
      idempotencyKey: "cancel-operation-1",
    }),
    {
      ok: true,
      status: 200,
      previewId: result.preview.id,
      canceled: true,
    },
  );
  assert.deepEqual(
    workflow.apply({
      previewId: result.preview.id,
      candidateId: result.preview.candidates[0].id,
      element,
      expectedRevision: 2,
      currentRevision: 2,
      idempotencyKey: "cancel-preview-1",
    }),
    {
      ok: false,
      status: 409,
      code: "template_v2_ai_rewrite_preview_closed",
    },
  );
  assert.equal(((element.runs as JsonRecord[])[0]).text, "매출은 빠르게 증가했습니다.");
});

test("reducer applies one bounded patch through existing undo/redo and dirty paths", () => {
  const selection: StudioSelection = {
    layoutId: "layout-1",
    componentId: "component-1",
    elementPath: [0],
  };
  const layouts: JsonRecord = {
    layouts: [
      {
        id: "layout-1",
        components: [
          {
            id: "component-1",
            elements: [textElement()],
            future_component_field: "retained",
          },
        ],
      },
    ],
    future_root_field: { retained: true },
  };
  const loaded = templateV2StudioReducer(EMPTY_TEMPLATE_V2_STUDIO_STATE, {
    type: "load",
    layouts,
  });
  const patched = templateV2StudioReducer(loaded, {
    type: "apply-text-selection-patch",
    selection,
    patch: {
      runIndex: 0,
      start: 0,
      end: 2,
      expectedRunText: "매출은 빠르게 증가했습니다.",
      replacement: "매출 증가",
    },
    historyKey: "ai-rewrite:preview-1",
  });

  assert.equal(
    ((getSelectedElement(patched.layouts, selection)?.runs as JsonRecord[])[0])
      .text,
    "매출 증가은 빠르게 증가했습니다.",
  );
  assert.equal(patched.dirty, true);
  assert.equal(patched.past.length, 1);
  assert.deepEqual(patched.layouts?.future_root_field, { retained: true });

  const replayed = templateV2StudioReducer(patched, {
    type: "apply-text-selection-patch",
    selection,
    patch: {
      runIndex: 0,
      start: 0,
      end: 2,
      expectedRunText: "매출은 빠르게 증가했습니다.",
      replacement: "매출 증가",
    },
    historyKey: "ai-rewrite:preview-1",
  });
  assert.equal(replayed, patched);

  const undone = templateV2StudioReducer(patched, { type: "undo" });
  assert.equal(
    ((getSelectedElement(undone.layouts, selection)?.runs as JsonRecord[])[0])
      .text,
    "매출은 빠르게 증가했습니다.",
  );
  assert.equal(undone.dirty, false);
  const redone = templateV2StudioReducer(undone, { type: "redo" });
  assert.equal(
    ((getSelectedElement(redone.layouts, selection)?.runs as JsonRecord[])[0])
      .text,
    "매출 증가은 빠르게 증가했습니다.",
  );
});
