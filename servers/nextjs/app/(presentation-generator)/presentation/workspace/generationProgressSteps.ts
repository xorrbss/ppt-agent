// Pure step model for the generation checklist. No runtime imports so
// node:test can load it directly.

export type GenerationStepId = "prepare" | "author" | "render" | "persist" | "publish";
export type GenerationStepState = "done" | "active" | "pending";

export interface GenerationStep {
  id: GenerationStepId;
  state: GenerationStepState;
  current?: number;
  total?: number;
}

export interface GenerationActivityInput {
  pages: { authored: number; rendered: number; persisted: number; total: number | null };
  stage: string | null;
  startedAt: string | null;
  lastProgressAt: string | null;
}

export interface GenerationEventEvidence {
  stage?: string;
  current?: number;
  total?: number;
  lastProgressAt?: string;
}

export interface GenerationProgressModel {
  steps: GenerationStep[];
  elapsedMs: number | null;
  etaMs: number | null;
  /** Epoch ms of the latest known progress, or null. */
  lastActivityAt: number | null;
}

export const ETA_MIN_RENDERED = 3;
export const ETA_MIN_ELAPSED_MS = 2 * 60_000;

const STEP_ORDER: GenerationStepId[] = ["prepare", "author", "render", "persist", "publish"];

const PREPARE_STAGES = new Set([
  "generation_started", "admission", "content_brief", "outline", "header", "authored_outline",
]);
const PERSIST_STAGES = new Set(["persist", "checkpoint", "repair"]);
const PUBLISH_STAGES = new Set(["export", "publication"]);

/** Event stages are public names (`authored_author`); activity stages are raw (`author`). */
export function normalizeGenerationStage(stage: string | null | undefined): string | null {
  const raw = stage?.trim();
  if (!raw) return null;
  const lower = raw.toLowerCase();
  if (lower === "authored_outline") return "outline";
  return lower.startsWith("authored_") ? lower.slice("authored_".length) : lower;
}

function stageStep(stage: string | null): GenerationStepId | null {
  if (!stage) return null;
  if (PREPARE_STAGES.has(stage) || stage === "outline") return "prepare";
  if (stage === "author" || stage === "illustrate") return "author";
  if (stage === "render") return "render";
  if (PERSIST_STAGES.has(stage)) return "persist";
  if (PUBLISH_STAGES.has(stage)) return "publish";
  return null;
}

function parseMs(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function positive(value: number | undefined | null): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

export function buildGenerationProgress(input: {
  activity: GenerationActivityInput | null;
  evidence: GenerationEventEvidence;
  now: number;
}): GenerationProgressModel {
  const { activity, evidence, now } = input;
  const stage = normalizeGenerationStage(activity?.stage) ?? normalizeGenerationStage(evidence.stage);
  const stageId = stageStep(stage);
  const eventStageId = stageStep(normalizeGenerationStage(evidence.stage));
  const eventCurrent = positive(evidence.current);
  const eventTotal = positive(evidence.total);

  let authored = activity?.pages.authored ?? 0;
  let rendered = activity?.pages.rendered ?? 0;
  let persisted = activity?.pages.persisted ?? 0;
  let total: number | null = activity?.pages.total ?? null;

  if (!activity && eventCurrent !== null && eventTotal !== null) {
    total = eventTotal;
    if (eventStageId === "author") authored = eventCurrent;
    else if (eventStageId === "render") rendered = eventCurrent;
    else if (eventStageId === "persist") persisted = eventCurrent;
  } else if (activity && total === null && eventTotal !== null) {
    total = eventTotal;
  }

  const hasTotal = total !== null && total > 0;
  const authorDone = hasTotal && authored >= (total as number);
  const renderDone = hasTotal && rendered >= (total as number);
  const persistDone = hasTotal && persisted >= (total as number);
  const stageIndex = stageId ? STEP_ORDER.indexOf(stageId) : -1;
  const publishing = stageId === "publish";

  const prepareDone = authored > 0 || rendered > 0 || persisted > 0 || stageIndex > 0;
  const persistActive = !persistDone && !publishing &&
    (stageId === "persist" || renderDone);
  const authorState: GenerationStepState = authorDone || renderDone || persistDone || publishing || stageIndex > 2 ||
      (stageId === "render" && authored === 0)
    ? "done"
    : authored > 0 || stageId === "author" ? "active" : "pending";
  const renderState: GenerationStepState = renderDone || publishing || persistDone
    ? "done"
    : rendered > 0 || stageId === "render" ? "active" : "pending";
  const persistState: GenerationStepState = persistDone || publishing
    ? "done" : persistActive || persisted > 0 ? "active" : "pending";
  const publishState: GenerationStepState = publishing ? "active" : "pending";

  const counted = (value: number, state: GenerationStepState) =>
    hasTotal ? { current: state === "done" ? (total as number) : Math.min(value, total as number), total: total as number } : {};

  const steps: GenerationStep[] = [
    { id: "prepare", state: prepareDone ? "done" : "active" },
    { id: "author", state: authorState, ...counted(authored, authorState) },
    { id: "render", state: renderState, ...counted(rendered, renderState) },
    { id: "persist", state: persistState, ...counted(persisted, persistState) },
    { id: "publish", state: publishState },
  ];

  const startedMs = parseMs(activity?.startedAt);
  const elapsedMs = startedMs === null ? null : Math.max(0, now - startedMs);
  let etaMs: number | null = null;
  if (elapsedMs !== null && hasTotal && rendered >= ETA_MIN_RENDERED &&
      elapsedMs >= ETA_MIN_ELAPSED_MS && rendered < (total as number)) {
    etaMs = ((total as number) - rendered) / (rendered / elapsedMs);
  }

  const candidates = [parseMs(activity?.lastProgressAt), parseMs(evidence.lastProgressAt)]
    .filter((value): value is number => value !== null);
  const lastActivityAt = candidates.length ? Math.max(...candidates) : null;
  return { steps, elapsedMs, etaMs, lastActivityAt };
}

/** Whole minutes for display; null below one minute means "almost done". */
export function etaMinutes(etaMs: number): number | null {
  const minutes = Math.round(etaMs / 60_000);
  return minutes >= 1 ? minutes : null;
}

export function activeGenerationStep(model: GenerationProgressModel): GenerationStepId | null {
  const active = model.steps.filter((step) => step.state === "active");
  return (active.at(-1) ?? null)?.id ?? null;
}

/** Latest stage/count seen in progress events (most recent sequence wins). */
export function latestGenerationEventEvidence(
  events: ReadonlyArray<{ type: string; sequence: number; stage?: string; current?: number; total?: number; createdAt?: string; error?: string }>,
): GenerationEventEvidence {
  const progress = events
    .filter((event) => ["operation.progress", "progress"].includes(event.type) && !event.error && event.stage)
    .sort((a, b) => a.sequence - b.sequence);
  const last = progress.at(-1);
  return last
    ? { stage: last.stage, current: last.current, total: last.total, lastProgressAt: last.createdAt }
    : {};
}
