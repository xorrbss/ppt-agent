import type { JsonRecord } from "./template-v2-studio.ts";

export const TEMPLATE_V2_AI_REWRITE_MAX_SELECTION_LENGTH = 8_192;
export const TEMPLATE_V2_AI_REWRITE_MAX_REPLACEMENT_LENGTH = 16_384;

export type TemplateV2AiRewriteAction =
  | { kind: "shorten" }
  | { kind: "expand" }
  | { kind: "rewrite" }
  | { kind: "translate"; targetLocale: string };

export interface TemplateV2TextSelection {
  runIndex: number;
  /** UTF-16 offsets which must also be grapheme boundaries. */
  start: number;
  end: number;
}

export interface TemplateV2TextSelectionPatch
  extends TemplateV2TextSelection {
  expectedRunText: string;
  replacement: string;
}

export type TemplateV2TextPatchReason =
  | "template_v2_ai_rewrite_not_text"
  | "template_v2_ai_rewrite_invalid_run"
  | "template_v2_ai_rewrite_invalid_font"
  | "template_v2_ai_rewrite_invalid_range"
  | "template_v2_ai_rewrite_not_grapheme_boundary"
  | "template_v2_ai_rewrite_selection_too_large"
  | "template_v2_ai_rewrite_replacement_too_large"
  | "template_v2_ai_rewrite_source_changed"
  | "template_v2_ai_rewrite_min_length_unavailable"
  | "template_v2_ai_rewrite_min_length_not_met"
  | "template_v2_ai_rewrite_max_length_unavailable"
  | "template_v2_ai_rewrite_max_length_exceeded";

export type TemplateV2TextPatchResult =
  | {
      ok: true;
      element: JsonRecord;
      selectedText: string;
      patch: TemplateV2TextSelectionPatch;
    }
  | {
      ok: false;
      element: JsonRecord;
      reason: TemplateV2TextPatchReason;
    };

export interface TemplateV2AiRewriteProviderRequest {
  action: TemplateV2AiRewriteAction;
  selectedText: string;
  candidateCount: 2 | 3;
}

export interface TemplateV2AiRewriteProvider {
  readonly kind: "deterministic-fixture";
  generate(
    request: TemplateV2AiRewriteProviderRequest
  ): Promise<readonly string[]>;
}

export interface TemplateV2AiRewriteCandidate {
  id: string;
  text: string;
  applyable: boolean;
  reason:
    | "template_v2_ai_rewrite_text_fits"
    | "template_v2_ai_rewrite_min_length_not_met"
    | "template_v2_ai_rewrite_max_length_exceeded";
}

export interface TemplateV2AiRewritePreview {
  id: string;
  targetId: string;
  expectedRevision: number;
  idempotencyKey: string;
  action: TemplateV2AiRewriteAction;
  selection: TemplateV2TextSelection;
  sourceRunText: string;
  selectedText: string;
  candidates: readonly TemplateV2AiRewriteCandidate[];
}

export type TemplateV2AiRewriteErrorCode =
  | TemplateV2TextPatchReason
  | "template_v2_ai_rewrite_provider_unavailable"
  | "template_v2_ai_rewrite_invalid_revision"
  | "template_v2_ai_rewrite_stale_revision"
  | "template_v2_ai_rewrite_invalid_idempotency_key"
  | "template_v2_ai_rewrite_idempotency_conflict"
  | "template_v2_ai_rewrite_invalid_target"
  | "template_v2_ai_rewrite_invalid_action"
  | "template_v2_ai_rewrite_invalid_candidates"
  | "template_v2_ai_rewrite_preview_not_found"
  | "template_v2_ai_rewrite_candidate_not_found"
  | "template_v2_ai_rewrite_candidate_underflow"
  | "template_v2_ai_rewrite_candidate_overflow";

export interface TemplateV2AiRewriteFailure {
  ok: false;
  status: 400 | 404 | 409 | 422 | 503;
  code: TemplateV2AiRewriteErrorCode;
}

export interface TemplateV2AiRewritePreviewSuccess {
  ok: true;
  status: 200;
  preview: TemplateV2AiRewritePreview;
}

export interface TemplateV2AiRewriteApplySuccess {
  ok: true;
  status: 200;
  element: JsonRecord;
  patch: TemplateV2TextSelectionPatch;
  historyKey: string;
  autosave: {
    expected_revision: number;
    idempotency_key: string;
  };
}

export interface TemplateV2AiRewritePreviewInput {
  targetId: string;
  element: JsonRecord;
  selection: TemplateV2TextSelection;
  action: TemplateV2AiRewriteAction;
  expectedRevision: number;
  currentRevision: number;
  idempotencyKey: string;
  candidateCount?: 2 | 3;
}

export interface TemplateV2AiRewriteApplyInput {
  previewId: string;
  candidateId: string;
  element: JsonRecord;
  expectedRevision: number;
  currentRevision: number;
  idempotencyKey: string;
}

export interface TemplateV2AiRewriteWorkflow {
  preview(
    input: TemplateV2AiRewritePreviewInput
  ): Promise<TemplateV2AiRewritePreviewSuccess | TemplateV2AiRewriteFailure>;
  apply(
    input: TemplateV2AiRewriteApplyInput
  ): TemplateV2AiRewriteApplySuccess | TemplateV2AiRewriteFailure;
}

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function validOptionalFont(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  if (!isRecord(value)) return false;
  const numberFields = [
    "size",
    "line_height",
    "letter_spacing",
    "opacity",
  ] as const;
  const stringFields = ["family", "color"] as const;
  const booleanFields = [
    "bold",
    "italic",
    "underline",
    "ellipsis",
  ] as const;
  return (
    numberFields.every(
      (field) =>
        value[field] === undefined ||
        value[field] === null ||
        (typeof value[field] === "number" && Number.isFinite(value[field]))
    ) &&
    stringFields.every(
      (field) =>
        value[field] === undefined ||
        value[field] === null ||
        typeof value[field] === "string"
    ) &&
    booleanFields.every(
      (field) =>
        value[field] === undefined ||
        value[field] === null ||
        typeof value[field] === "boolean"
    )
  );
}

function elementFingerprint(element: JsonRecord): string | null {
  try {
    const serialized = JSON.stringify(element);
    return typeof serialized === "string" ? serialized : null;
  } catch {
    return null;
  }
}

function failure(
  status: TemplateV2AiRewriteFailure["status"],
  code: TemplateV2AiRewriteErrorCode
): TemplateV2AiRewriteFailure {
  return { ok: false, status, code };
}

function graphemeBoundaries(text: string): Set<number> | null {
  if (typeof Intl.Segmenter !== "function") return null;
  const boundaries = new Set<number>([0, text.length]);
  for (const segment of new Intl.Segmenter("und", {
    granularity: "grapheme",
  }).segment(text)) {
    boundaries.add(segment.index);
  }
  return boundaries;
}

function selectionError(
  text: string,
  selection: TemplateV2TextSelection
): TemplateV2TextPatchReason | null {
  if (
    !Number.isSafeInteger(selection.start) ||
    !Number.isSafeInteger(selection.end) ||
    selection.start < 0 ||
    selection.end <= selection.start ||
    selection.end > text.length
  ) {
    return "template_v2_ai_rewrite_invalid_range";
  }
  if (
    selection.end - selection.start >
    TEMPLATE_V2_AI_REWRITE_MAX_SELECTION_LENGTH
  ) {
    return "template_v2_ai_rewrite_selection_too_large";
  }
  const boundaries = graphemeBoundaries(text);
  if (
    !boundaries ||
    !boundaries.has(selection.start) ||
    !boundaries.has(selection.end)
  ) {
    return "template_v2_ai_rewrite_not_grapheme_boundary";
  }
  return null;
}

function codePointLength(text: string): number {
  return Array.from(text).length;
}

export function applyTemplateV2TextSelectionPatch(
  element: JsonRecord,
  patch: TemplateV2TextSelectionPatch
): TemplateV2TextPatchResult {
  if (element.type !== "text" || !Array.isArray(element.runs)) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_not_text",
    };
  }
  if (!validOptionalFont(element.font)) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_invalid_font",
    };
  }
  for (const value of element.runs) {
    if (!isRecord(value) || typeof value.text !== "string") {
      return {
        ok: false,
        element,
        reason: "template_v2_ai_rewrite_invalid_run",
      };
    }
    if (!validOptionalFont(value.font)) {
      return {
        ok: false,
        element,
        reason: "template_v2_ai_rewrite_invalid_font",
      };
    }
  }
  if (
    !Number.isSafeInteger(patch.runIndex) ||
    patch.runIndex < 0 ||
    patch.runIndex >= element.runs.length
  ) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_invalid_run",
    };
  }
  const run = element.runs[patch.runIndex];
  if (!isRecord(run) || typeof run.text !== "string") {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_invalid_run",
    };
  }
  if (run.text !== patch.expectedRunText) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_source_changed",
    };
  }
  const invalidSelection = selectionError(run.text, patch);
  if (invalidSelection) {
    return { ok: false, element, reason: invalidSelection };
  }
  if (
    codePointLength(patch.replacement) >
    TEMPLATE_V2_AI_REWRITE_MAX_REPLACEMENT_LENGTH
  ) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_replacement_too_large",
    };
  }
  if (
    !Number.isSafeInteger(element.max_length) ||
    Number(element.max_length) < 0
  ) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_max_length_unavailable",
    };
  }
  if (
    !Number.isSafeInteger(element.min_length) ||
    Number(element.min_length) < 0 ||
    Number(element.min_length) > Number(element.max_length)
  ) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_min_length_unavailable",
    };
  }

  const nextRun = {
    ...run,
    text:
      run.text.slice(0, patch.start) +
      patch.replacement +
      run.text.slice(patch.end),
  };
  const runs = element.runs.slice();
  runs[patch.runIndex] = nextRun;
  const totalLength = runs.reduce(
    (total, value) => total + codePointLength((value as JsonRecord).text as string),
    0
  );
  if (totalLength < Number(element.min_length)) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_min_length_not_met",
    };
  }
  if (totalLength > Number(element.max_length)) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_max_length_exceeded",
    };
  }

  return {
    ok: true,
    element: { ...element, runs },
    selectedText: run.text.slice(patch.start, patch.end),
    patch: { ...patch },
  };
}

function validAction(action: TemplateV2AiRewriteAction): boolean {
  if (!isRecord(action)) return false;
  const keys = Object.keys(action);
  if (["shorten", "expand", "rewrite"].includes(String(action.kind))) {
    return keys.length === 1 && keys[0] === "kind";
  }
  return (
    action.kind === "translate" &&
    keys.length === 2 &&
    keys.includes("kind") &&
    keys.includes("targetLocale") &&
    typeof action.targetLocale === "string" &&
    action.targetLocale.trim().length > 0 &&
    action.targetLocale.length <= 64
  );
}

function validRevision(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 1;
}

function validIdempotencyKey(value: string): boolean {
  return (
    typeof value === "string" &&
    value.length >= 8 &&
    value.length <= 128 &&
    /^[A-Za-z0-9._:-]+$/.test(value)
  );
}

function stableId(value: string): string {
  let hash = 2_166_136_261;
  for (const character of value) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16_777_619);
  }
  return (hash >>> 0).toString(36);
}

function candidatePatch(
  preview: Pick<
    TemplateV2AiRewritePreview,
    "selection" | "sourceRunText"
  >,
  replacement: string
): TemplateV2TextSelectionPatch {
  return {
    ...preview.selection,
    expectedRunText: preview.sourceRunText,
    replacement,
  };
}

export function createTemplateV2DeterministicRewriteProvider(
  candidates: readonly string[]
): TemplateV2AiRewriteProvider {
  if (!Array.isArray(candidates)) {
    throw new TypeError(
      "Template V2 deterministic rewrites require a fixed candidate array"
    );
  }
  const fixedCandidates = Object.freeze([...candidates]);
  const provider: TemplateV2AiRewriteProvider = Object.freeze({
    kind: "deterministic-fixture" as const,
    async generate() {
      return fixedCandidates.slice();
    },
  });
  trustedDeterministicProviders.add(provider);
  return provider;
}

const trustedDeterministicProviders =
  new WeakSet<TemplateV2AiRewriteProvider>();

export function createTemplateV2AiRewriteWorkflow(
  provider?: TemplateV2AiRewriteProvider
): TemplateV2AiRewriteWorkflow {
  const previews = new Map<string, TemplateV2AiRewritePreview>();
  const previewKeys = new Map<
    string,
    { fingerprint: string; preview: TemplateV2AiRewritePreview }
  >();
  const applied = new Map<
    string,
    {
      inputFingerprint: string;
      success: TemplateV2AiRewriteApplySuccess;
    }
  >();

  return {
    async preview(input) {
      if (!provider || !trustedDeterministicProviders.has(provider)) {
        return failure(
          503,
          "template_v2_ai_rewrite_provider_unavailable"
        );
      }
      if (
        !validRevision(input.expectedRevision) ||
        !validRevision(input.currentRevision)
      ) {
        return failure(400, "template_v2_ai_rewrite_invalid_revision");
      }
      if (input.expectedRevision !== input.currentRevision) {
        return failure(409, "template_v2_ai_rewrite_stale_revision");
      }
      if (!validIdempotencyKey(input.idempotencyKey)) {
        return failure(
          400,
          "template_v2_ai_rewrite_invalid_idempotency_key"
        );
      }
      if (
        typeof input.targetId !== "string" ||
        input.targetId.trim().length === 0 ||
        input.targetId.length > 512
      ) {
        return failure(400, "template_v2_ai_rewrite_invalid_target");
      }
      if (!validAction(input.action)) {
        return failure(400, "template_v2_ai_rewrite_invalid_action");
      }
      const candidateCount = input.candidateCount ?? 2;
      if (candidateCount !== 2 && candidateCount !== 3) {
        return failure(400, "template_v2_ai_rewrite_invalid_candidates");
      }

      const validation = applyTemplateV2TextSelectionPatch(input.element, {
        ...input.selection,
        expectedRunText:
          input.element.type === "text" &&
          Array.isArray(input.element.runs) &&
          isRecord(input.element.runs[input.selection.runIndex]) &&
          typeof input.element.runs[input.selection.runIndex].text === "string"
            ? input.element.runs[input.selection.runIndex].text
            : "",
        replacement:
          input.element.type === "text" &&
          Array.isArray(input.element.runs) &&
          isRecord(input.element.runs[input.selection.runIndex]) &&
          typeof input.element.runs[input.selection.runIndex].text === "string"
            ? input.element.runs[input.selection.runIndex].text.slice(
                input.selection.start,
                input.selection.end
              )
            : "",
      });
      if (!validation.ok) {
        return failure(422, validation.reason);
      }

      const fingerprint = JSON.stringify({
        targetId: input.targetId,
        action: input.action,
        selection: input.selection,
        expectedRevision: input.expectedRevision,
        sourceRunText: validation.patch.expectedRunText,
        candidateCount,
      });
      const previous = previewKeys.get(input.idempotencyKey);
      if (previous) {
        if (previous.fingerprint !== fingerprint) {
          return failure(
            409,
            "template_v2_ai_rewrite_idempotency_conflict"
          );
        }
        return { ok: true, status: 200, preview: previous.preview };
      }

      let generated: readonly string[];
      try {
        generated = await provider.generate({
          action: input.action,
          selectedText: validation.selectedText,
          candidateCount,
        });
      } catch {
        return failure(
          503,
          "template_v2_ai_rewrite_provider_unavailable"
        );
      }
      if (
        !Array.isArray(generated) ||
        generated.length !== candidateCount ||
        generated.some(
          (text) =>
            typeof text !== "string" ||
            text.length === 0 ||
            codePointLength(text) >
              TEMPLATE_V2_AI_REWRITE_MAX_REPLACEMENT_LENGTH
        ) ||
        new Set(generated).size !== generated.length
      ) {
        return failure(422, "template_v2_ai_rewrite_invalid_candidates");
      }

      const previewBase = {
        selection: { ...input.selection },
        sourceRunText: validation.patch.expectedRunText,
      };
      const candidates = generated.map((text, index) => {
        const result = applyTemplateV2TextSelectionPatch(
          input.element,
          candidatePatch(previewBase, text)
        );
        const fits = result.ok;
        const reason = result.ok
          ? ("template_v2_ai_rewrite_text_fits" as const)
          : result.reason === "template_v2_ai_rewrite_min_length_not_met"
            ? ("template_v2_ai_rewrite_min_length_not_met" as const)
            : ("template_v2_ai_rewrite_max_length_exceeded" as const);
        return {
          id: `candidate-${index + 1}-${stableId(text)}`,
          text,
          applyable: fits,
          reason,
        };
      });
      const preview: TemplateV2AiRewritePreview = {
        id: `preview-${stableId(
          `${input.idempotencyKey}:${input.targetId}:${fingerprint}`
        )}`,
        targetId: input.targetId,
        expectedRevision: input.expectedRevision,
        idempotencyKey: input.idempotencyKey,
        action: input.action,
        selection: { ...input.selection },
        sourceRunText: validation.patch.expectedRunText,
        selectedText: validation.selectedText,
        candidates,
      };
      previews.set(preview.id, preview);
      previewKeys.set(input.idempotencyKey, { fingerprint, preview });
      return { ok: true, status: 200, preview };
    },

    apply(input) {
      if (
        !validRevision(input.expectedRevision) ||
        !validRevision(input.currentRevision)
      ) {
        return failure(400, "template_v2_ai_rewrite_invalid_revision");
      }
      if (input.expectedRevision !== input.currentRevision) {
        return failure(409, "template_v2_ai_rewrite_stale_revision");
      }
      if (!validIdempotencyKey(input.idempotencyKey)) {
        return failure(
          400,
          "template_v2_ai_rewrite_invalid_idempotency_key"
        );
      }
      const preview = previews.get(input.previewId);
      if (!preview) {
        return failure(404, "template_v2_ai_rewrite_preview_not_found");
      }
      if (
        preview.expectedRevision !== input.expectedRevision ||
        preview.idempotencyKey !== input.idempotencyKey
      ) {
        return failure(
          409,
          "template_v2_ai_rewrite_idempotency_conflict"
        );
      }
      const candidate = preview.candidates.find(
        (value) => value.id === input.candidateId
      );
      if (!candidate) {
        return failure(404, "template_v2_ai_rewrite_candidate_not_found");
      }
      if (!candidate.applyable) {
        return failure(
          409,
          candidate.reason === "template_v2_ai_rewrite_min_length_not_met"
            ? "template_v2_ai_rewrite_candidate_underflow"
            : "template_v2_ai_rewrite_candidate_overflow"
        );
      }
      const applyKey = `${preview.id}:${candidate.id}:${input.idempotencyKey}`;
      const patch = candidatePatch(preview, candidate.text);
      const result = applyTemplateV2TextSelectionPatch(input.element, patch);
      if (!result.ok) {
        return failure(
          result.reason === "template_v2_ai_rewrite_source_changed"
            ? 409
            : 422,
          result.reason
        );
      }
      const inputFingerprint = elementFingerprint(input.element);
      if (inputFingerprint === null) {
        return failure(409, "template_v2_ai_rewrite_source_changed");
      }
      const previous = applied.get(applyKey);
      if (previous) {
        if (previous.inputFingerprint !== inputFingerprint) {
          return failure(409, "template_v2_ai_rewrite_source_changed");
        }
        return previous.success;
      }
      const success: TemplateV2AiRewriteApplySuccess = {
        ok: true,
        status: 200,
        element: result.element,
        patch,
        historyKey: `ai-rewrite:${preview.id}`,
        autosave: {
          expected_revision: input.expectedRevision,
          idempotency_key: input.idempotencyKey,
        },
      };
      applied.set(applyKey, { inputFingerprint, success });
      return success;
    },
  };
}
