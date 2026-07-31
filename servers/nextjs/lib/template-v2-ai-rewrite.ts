export type JsonRecord = Record<string, unknown>;

export const TEMPLATE_V2_AI_REWRITE_MAX_SELECTION_LENGTH = 8_192;
export const TEMPLATE_V2_AI_REWRITE_MAX_REPLACEMENT_LENGTH = 16_384;

export type TemplateV2AiRewriteAction =
  | { kind: "shorten" }
  | { kind: "expand" }
  | { kind: "tone-report" }
  | { kind: "tone-proposal" }
  | { kind: "translate"; targetLocale: string };

export interface TemplateV2TextSelection {
  runIndex: number;
  /** UTF-16 offset. It must also be an Intl.Segmenter grapheme boundary. */
  start: number;
  /** UTF-16 offset. It must also be an Intl.Segmenter grapheme boundary. */
  end: number;
}

export interface TemplateV2TextSelectionPatch extends TemplateV2TextSelection {
  expectedRunText: string;
  replacement: string;
}

export type TemplateV2TextPatchReason =
  | "template_v2_ai_rewrite_not_text"
  | "template_v2_ai_rewrite_invalid_run"
  | "template_v2_ai_rewrite_invalid_range"
  | "template_v2_ai_rewrite_not_grapheme_boundary"
  | "template_v2_ai_rewrite_selection_too_large"
  | "template_v2_ai_rewrite_replacement_too_large"
  | "template_v2_ai_rewrite_source_changed";

export type TemplateV2TextPatchResult =
  | {
      ok: true;
      element: JsonRecord;
      selectedText: string;
      patch: TemplateV2TextSelectionPatch;
    }
  | { ok: false; element: JsonRecord; reason: TemplateV2TextPatchReason };

export interface TemplateV2TextFitPreflight {
  status: "fits" | "overflow" | "unavailable";
  reason:
    | "template_v2_ai_rewrite_text_fits"
    | "template_v2_ai_rewrite_text_overflow"
    | "template_v2_ai_rewrite_max_length_exceeded"
    | "template_v2_ai_rewrite_missing_text_geometry";
  estimatedLines: number | null;
  availableLines: number | null;
}

export interface TemplateV2AiRewriteProviderRequest {
  action: TemplateV2AiRewriteAction;
  selectedText: string;
  candidateCount: 2 | 3;
}

export interface TemplateV2AiRewriteProvider {
  /**
   * Production/network providers are intentionally not accepted by this
   * boundary. Tests and approved local development must inject an explicit
   * deterministic fake.
   */
  kind: "deterministic-fake";
  generate(
    request: TemplateV2AiRewriteProviderRequest,
  ): Promise<readonly string[]>;
}

export interface TemplateV2AiRewriteDiff {
  before: string;
  after: string;
  unchangedPrefix: string;
  removed: string;
  inserted: string;
  unchangedSuffix: string;
}

export interface TemplateV2AiRewriteCandidate {
  id: string;
  text: string;
  diff: TemplateV2AiRewriteDiff;
  preflight: TemplateV2TextFitPreflight;
  applyable: boolean;
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
  status: "preview";
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
  | "template_v2_ai_rewrite_preview_closed"
  | "template_v2_ai_rewrite_candidate_not_found"
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

export interface TemplateV2AiRewriteCancelSuccess {
  ok: true;
  status: 200;
  previewId: string;
  canceled: true;
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

export interface TemplateV2AiRewriteCancelInput {
  previewId: string;
  idempotencyKey: string;
}

export interface TemplateV2AiRewriteWorkflow {
  preview(
    input: TemplateV2AiRewritePreviewInput,
  ): Promise<TemplateV2AiRewritePreviewSuccess | TemplateV2AiRewriteFailure>;
  apply(
    input: TemplateV2AiRewriteApplyInput,
  ): TemplateV2AiRewriteApplySuccess | TemplateV2AiRewriteFailure;
  cancel(
    input: TemplateV2AiRewriteCancelInput,
  ): TemplateV2AiRewriteCancelSuccess | TemplateV2AiRewriteFailure;
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function deepEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) {
      return false;
    }
    return left.every((value, index) => deepEqual(value, right[index]));
  }
  if (!isRecord(left) || !isRecord(right)) return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return (
    leftKeys.length === rightKeys.length &&
    leftKeys.every(
      (key, index) =>
        key === rightKeys[index] && deepEqual(left[key], right[key]),
    )
  );
}

function sameRunMetadata(left: JsonRecord, right: JsonRecord): boolean {
  const leftMetadata = { ...left };
  const rightMetadata = { ...right };
  delete leftMetadata.text;
  delete rightMetadata.text;
  return deepEqual(leftMetadata, rightMetadata);
}

function mergeAdjacentRuns(runs: readonly unknown[]): unknown[] {
  const merged: unknown[] = [];
  for (const value of runs) {
    const previous = merged.at(-1);
    if (
      isRecord(previous) &&
      isRecord(value) &&
      typeof previous.text === "string" &&
      typeof value.text === "string" &&
      sameRunMetadata(previous, value)
    ) {
      merged[merged.length - 1] = {
        ...previous,
        text: previous.text + value.text,
      };
    } else {
      merged.push(value);
    }
  }
  return merged;
}

function graphemeBoundaries(text: string): Set<number> | null {
  if (typeof Intl.Segmenter !== "function") return null;
  const boundaries = new Set<number>([0, text.length]);
  for (const part of new Intl.Segmenter("und", {
    granularity: "grapheme",
  }).segment(text)) {
    boundaries.add(part.index);
  }
  return boundaries;
}

function validateSelection(
  text: string,
  selection: TemplateV2TextSelection,
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

export function applyTemplateV2TextSelectionPatch(
  element: JsonRecord,
  patch: TemplateV2TextSelectionPatch,
): TemplateV2TextPatchResult {
  if (element.type !== "text" || !Array.isArray(element.runs)) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_not_text",
    };
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
  const selectionError = validateSelection(run.text, patch);
  if (selectionError) return { ok: false, element, reason: selectionError };
  if (
    patch.replacement.length >
    TEMPLATE_V2_AI_REWRITE_MAX_REPLACEMENT_LENGTH
  ) {
    return {
      ok: false,
      element,
      reason: "template_v2_ai_rewrite_replacement_too_large",
    };
  }

  const fragments: JsonRecord[] = [];
  const before = run.text.slice(0, patch.start);
  const after = run.text.slice(patch.end);
  if (before) fragments.push({ ...run, text: before });
  fragments.push({ ...run, text: patch.replacement });
  if (after) fragments.push({ ...run, text: after });

  const runs = element.runs.slice();
  runs.splice(patch.runIndex, 1, ...fragments);
  const nextRuns = mergeAdjacentRuns(runs);
  const selectedText = run.text.slice(patch.start, patch.end);
  return {
    ok: true,
    element: { ...element, runs: nextRuns },
    selectedText,
    patch: { ...patch },
  };
}

function numericField(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null;
}

function runFontMetrics(run: JsonRecord): { size: number; lineHeight: number } {
  const font = isRecord(run.font) ? run.font : {};
  const size = numericField(font.size) ?? 18;
  return {
    size,
    lineHeight: numericField(font.line_height) ?? size * 1.2,
  };
}

function graphemeWidth(grapheme: string): number {
  if (/^\s+$/u.test(grapheme)) return 0.33;
  if (/^\p{Mark}+$/u.test(grapheme)) return 0;
  if (/^[\u0000-\u007f]+$/u.test(grapheme)) return 0.56;
  return 1;
}

export function preflightTemplateV2TextFit(
  element: JsonRecord,
): TemplateV2TextFitPreflight {
  const size = isRecord(element.size) ? element.size : {};
  const width = numericField(size.width);
  const height = numericField(size.height);
  if (
    element.type !== "text" ||
    !Array.isArray(element.runs) ||
    !width ||
    !height
  ) {
    return {
      status: "unavailable",
      reason: "template_v2_ai_rewrite_missing_text_geometry",
      estimatedLines: null,
      availableLines: null,
    };
  }

  const runs = element.runs.filter(
    (run): run is JsonRecord => isRecord(run) && typeof run.text === "string",
  );
  if (
    runs.length !== element.runs.length ||
    typeof Intl.Segmenter !== "function"
  ) {
    return {
      status: "unavailable",
      reason: "template_v2_ai_rewrite_missing_text_geometry",
      estimatedLines: null,
      availableLines: null,
    };
  }
  const plainText = runs.map((run) => String(run.text)).join("");
  if (
    typeof element.max_length === "number" &&
    Number.isSafeInteger(element.max_length) &&
    plainText.length > element.max_length
  ) {
    return {
      status: "overflow",
      reason: "template_v2_ai_rewrite_max_length_exceeded",
      estimatedLines: null,
      availableLines: null,
    };
  }

  const largestLineHeight = Math.max(
    1,
    ...runs.map((run) => runFontMetrics(run).lineHeight),
  );
  const availableLines = Math.max(1, Math.floor(height / largestLineHeight));
  let estimatedLines = 1;
  let currentWidth = 0;
  const segmenter = new Intl.Segmenter("und", { granularity: "grapheme" });
  for (const run of runs) {
    const fontSize = runFontMetrics(run).size;
    for (const part of segmenter.segment(String(run.text))) {
      if (/^(?:\r\n|[\n\r])$/.test(part.segment)) {
        estimatedLines += 1;
        currentWidth = 0;
        continue;
      }
      const nextWidth = graphemeWidth(part.segment) * fontSize;
      if (currentWidth > 0 && currentWidth + nextWidth > width) {
        estimatedLines += 1;
        currentWidth = nextWidth;
      } else {
        currentWidth += nextWidth;
      }
    }
  }
  const fits = estimatedLines <= availableLines;
  return {
    status: fits ? "fits" : "overflow",
    reason: fits
      ? "template_v2_ai_rewrite_text_fits"
      : "template_v2_ai_rewrite_text_overflow",
    estimatedLines,
    availableLines,
  };
}

function splitGraphemes(text: string): string[] {
  if (typeof Intl.Segmenter !== "function") {
    return text.length > 0 ? [text] : [];
  }
  return [
    ...new Intl.Segmenter("und", { granularity: "grapheme" }).segment(text),
  ].map((part) => part.segment);
}

export function diffTemplateV2AiRewrite(
  before: string,
  after: string,
): TemplateV2AiRewriteDiff {
  const beforeParts = splitGraphemes(before);
  const afterParts = splitGraphemes(after);
  let prefixLength = 0;
  while (
    prefixLength < beforeParts.length &&
    prefixLength < afterParts.length &&
    beforeParts[prefixLength] === afterParts[prefixLength]
  ) {
    prefixLength += 1;
  }
  let suffixLength = 0;
  while (
    suffixLength < beforeParts.length - prefixLength &&
    suffixLength < afterParts.length - prefixLength &&
    beforeParts[beforeParts.length - 1 - suffixLength] ===
      afterParts[afterParts.length - 1 - suffixLength]
  ) {
    suffixLength += 1;
  }
  const suffixStartBefore = beforeParts.length - suffixLength;
  const suffixStartAfter = afterParts.length - suffixLength;
  return {
    before,
    after,
    unchangedPrefix: beforeParts.slice(0, prefixLength).join(""),
    removed: beforeParts.slice(prefixLength, suffixStartBefore).join(""),
    inserted: afterParts.slice(prefixLength, suffixStartAfter).join(""),
    unchangedSuffix: beforeParts.slice(suffixStartBefore).join(""),
  };
}

function stableHash(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function stableStringify(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(",")}]`;
  }
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function validRevision(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 1;
}

function validIdempotencyKey(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value === value.trim() &&
    value.length >= 8 &&
    value.length <= 128 &&
    /^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(value)
  );
}

function validAction(action: unknown): action is TemplateV2AiRewriteAction {
  if (!isRecord(action) || typeof action.kind !== "string") return false;
  if (
    action.kind === "shorten" ||
    action.kind === "expand" ||
    action.kind === "tone-report" ||
    action.kind === "tone-proposal"
  ) {
    return true;
  }
  return (
    action.kind === "translate" &&
    typeof action.targetLocale === "string" &&
    action.targetLocale === action.targetLocale.trim() &&
    /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(action.targetLocale)
  );
}

function validSelectionShape(
  selection: unknown,
): selection is TemplateV2TextSelection {
  return (
    isRecord(selection) &&
    typeof selection.runIndex === "number" &&
    typeof selection.start === "number" &&
    typeof selection.end === "number"
  );
}

function failure(
  status: TemplateV2AiRewriteFailure["status"],
  code: TemplateV2AiRewriteErrorCode,
): TemplateV2AiRewriteFailure {
  return { ok: false, status, code };
}

export function createTemplateV2FakeAiRewriteProvider(
  generate: (
    request: TemplateV2AiRewriteProviderRequest,
  ) => readonly string[] | Promise<readonly string[]>,
): TemplateV2AiRewriteProvider {
  return {
    kind: "deterministic-fake",
    generate: async (request) => generate(request),
  };
}

type PreviewRecord = {
  preview: TemplateV2AiRewritePreview;
  state: "preview" | "applied" | "canceled";
  applied?: TemplateV2AiRewriteApplySuccess;
  appliedCandidateId?: string;
};

export function createTemplateV2AiRewriteWorkflow(
  provider?: TemplateV2AiRewriteProvider,
): TemplateV2AiRewriteWorkflow {
  const previews = new Map<string, PreviewRecord>();
  const requestSignatures = new Map<string, string>();
  const previewByRequest = new Map<string, TemplateV2AiRewritePreviewSuccess>();
  const cancelByRequest = new Map<string, TemplateV2AiRewriteCancelSuccess>();

  return {
    async preview(input) {
      if (!validRevision(input.expectedRevision) || !validRevision(input.currentRevision)) {
        return failure(422, "template_v2_ai_rewrite_invalid_revision");
      }
      if (input.expectedRevision !== input.currentRevision) {
        return failure(409, "template_v2_ai_rewrite_stale_revision");
      }
      if (!validIdempotencyKey(input.idempotencyKey)) {
        return failure(422, "template_v2_ai_rewrite_invalid_idempotency_key");
      }
      if (
        typeof input.targetId !== "string" ||
        input.targetId !== input.targetId.trim() ||
        input.targetId.length < 1 ||
        input.targetId.length > 256
      ) {
        return failure(422, "template_v2_ai_rewrite_invalid_target");
      }
      if (!validAction(input.action)) {
        return failure(422, "template_v2_ai_rewrite_invalid_action");
      }
      if (!validSelectionShape(input.selection)) {
        return failure(422, "template_v2_ai_rewrite_invalid_range");
      }
      const candidateCount = input.candidateCount ?? 3;
      if (candidateCount !== 2 && candidateCount !== 3) {
        return failure(422, "template_v2_ai_rewrite_invalid_candidates");
      }
      const signature = stableStringify({
        targetId: input.targetId,
        selection: input.selection,
        action: input.action,
        expectedRevision: input.expectedRevision,
        candidateCount,
      });
      const existingSignature = requestSignatures.get(input.idempotencyKey);
      if (existingSignature && existingSignature !== signature) {
        return failure(409, "template_v2_ai_rewrite_idempotency_conflict");
      }
      const existing = previewByRequest.get(input.idempotencyKey);
      if (existing) return existing;

      if (!provider || provider.kind !== "deterministic-fake") {
        return failure(503, "template_v2_ai_rewrite_provider_unavailable");
      }
      const runs = Array.isArray(input.element.runs) ? input.element.runs : [];
      const run = runs[input.selection.runIndex];
      if (input.element.type !== "text" || !isRecord(run) || typeof run.text !== "string") {
        return failure(422, "template_v2_ai_rewrite_invalid_run");
      }
      const selectionError = validateSelection(run.text, input.selection);
      if (selectionError) return failure(422, selectionError);
      const selectedText = run.text.slice(input.selection.start, input.selection.end);
      let generated: readonly string[];
      try {
        generated = await provider.generate({
          action: input.action,
          selectedText,
          candidateCount,
        });
      } catch {
        return failure(503, "template_v2_ai_rewrite_provider_unavailable");
      }
      if (
        !Array.isArray(generated) ||
        generated.length !== candidateCount ||
        generated.length < 2 ||
        generated.length > 3 ||
        new Set(generated).size !== generated.length ||
        generated.some(
          (text) =>
            typeof text !== "string" ||
            text.length === 0 ||
            text === selectedText ||
            text.length > TEMPLATE_V2_AI_REWRITE_MAX_REPLACEMENT_LENGTH,
        )
      ) {
        return failure(422, "template_v2_ai_rewrite_invalid_candidates");
      }

      const previewId = `rewrite-${stableHash(
        `${input.idempotencyKey}:${signature}:${run.text}`,
      )}`;
      const candidates = generated.map((text, index) => {
        const patched = applyTemplateV2TextSelectionPatch(input.element, {
          ...input.selection,
          expectedRunText: run.text as string,
          replacement: text,
        });
        const preflight = patched.ok
          ? preflightTemplateV2TextFit(patched.element)
          : {
              status: "unavailable" as const,
              reason: "template_v2_ai_rewrite_missing_text_geometry" as const,
              estimatedLines: null,
              availableLines: null,
            };
        return {
          id: `${previewId}-candidate-${index + 1}`,
          text,
          diff: diffTemplateV2AiRewrite(selectedText, text),
          preflight,
          applyable: preflight.status === "fits",
        };
      });
      const preview: TemplateV2AiRewritePreview = {
        id: previewId,
        targetId: input.targetId,
        expectedRevision: input.expectedRevision,
        idempotencyKey: input.idempotencyKey,
        action: structuredClone(input.action),
        selection: { ...input.selection },
        sourceRunText: run.text,
        selectedText,
        candidates,
        status: "preview",
      };
      const success: TemplateV2AiRewritePreviewSuccess = {
        ok: true,
        status: 200,
        preview,
      };
      requestSignatures.set(input.idempotencyKey, signature);
      previewByRequest.set(input.idempotencyKey, success);
      previews.set(preview.id, { preview, state: "preview" });
      return success;
    },

    apply(input) {
      if (!validRevision(input.expectedRevision) || !validRevision(input.currentRevision)) {
        return failure(422, "template_v2_ai_rewrite_invalid_revision");
      }
      if (input.expectedRevision !== input.currentRevision) {
        return failure(409, "template_v2_ai_rewrite_stale_revision");
      }
      if (!validIdempotencyKey(input.idempotencyKey)) {
        return failure(422, "template_v2_ai_rewrite_invalid_idempotency_key");
      }
      const record = previews.get(input.previewId);
      if (!record) return failure(404, "template_v2_ai_rewrite_preview_not_found");
      if (
        input.expectedRevision !== record.preview.expectedRevision ||
        input.idempotencyKey !== record.preview.idempotencyKey
      ) {
        return failure(409, "template_v2_ai_rewrite_idempotency_conflict");
      }
      if (record.state === "applied" && record.applied) {
        return record.appliedCandidateId === input.candidateId
          ? record.applied
          : failure(409, "template_v2_ai_rewrite_idempotency_conflict");
      }
      if (record.state !== "preview") {
        return failure(409, "template_v2_ai_rewrite_preview_closed");
      }
      const candidate = record.preview.candidates.find(
        (value) => value.id === input.candidateId,
      );
      if (!candidate) {
        return failure(404, "template_v2_ai_rewrite_candidate_not_found");
      }
      if (!candidate.applyable) {
        return failure(409, "template_v2_ai_rewrite_candidate_overflow");
      }
      const patch: TemplateV2TextSelectionPatch = {
        ...record.preview.selection,
        expectedRunText: record.preview.sourceRunText,
        replacement: candidate.text,
      };
      const patched = applyTemplateV2TextSelectionPatch(input.element, patch);
      if (!patched.ok) return failure(409, patched.reason);
      const preflight = preflightTemplateV2TextFit(patched.element);
      if (preflight.status !== "fits") {
        return failure(409, "template_v2_ai_rewrite_candidate_overflow");
      }
      const success: TemplateV2AiRewriteApplySuccess = {
        ok: true,
        status: 200,
        element: patched.element,
        patch,
        historyKey: `ai-rewrite:${record.preview.id}`,
        autosave: {
          expected_revision: record.preview.expectedRevision,
          idempotency_key: record.preview.idempotencyKey,
        },
      };
      record.state = "applied";
      record.applied = success;
      record.appliedCandidateId = input.candidateId;
      return success;
    },

    cancel(input) {
      if (!validIdempotencyKey(input.idempotencyKey)) {
        return failure(422, "template_v2_ai_rewrite_invalid_idempotency_key");
      }
      const existing = cancelByRequest.get(input.idempotencyKey);
      if (existing) {
        return existing.previewId === input.previewId
          ? existing
          : failure(409, "template_v2_ai_rewrite_idempotency_conflict");
      }
      const record = previews.get(input.previewId);
      if (!record) return failure(404, "template_v2_ai_rewrite_preview_not_found");
      if (record.state !== "preview") {
        return failure(409, "template_v2_ai_rewrite_preview_closed");
      }
      record.state = "canceled";
      const success: TemplateV2AiRewriteCancelSuccess = {
        ok: true,
        status: 200,
        previewId: input.previewId,
        canceled: true,
      };
      cancelByRequest.set(input.idempotencyKey, success);
      return success;
    },
  };
}
