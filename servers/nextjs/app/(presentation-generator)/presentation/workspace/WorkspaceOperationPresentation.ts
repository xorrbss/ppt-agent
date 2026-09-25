import type { WorkspaceOperationAnchor } from "./types";

export interface WorkspaceOperationObservation {
  lastConfirmedAt?: string;
  lastProgressAt?: string;
  connection?: "confirmed" | "checking";
}

export const GENERATION_FAILURE_MESSAGE =
  "생성을 완료하지 못했어요. 작업 상세를 확인하거나 요청을 수정해 주세요.";

const GENERATION_STAGE_LABELS: Record<string, string> = {
  generation_started: "생성을 시작하고 있습니다.",
  authored_outline: "발표 구조와 작성 기준을 준비하고 있습니다.",
  authored_author: "슬라이드 내용을 작성하고 있습니다.",
  authored_illustrate: "시각 요소를 구성하고 있습니다.",
  authored_render: "슬라이드를 렌더링하고 있습니다.",
  authored_checkpoint: "생성 결과를 확인하고 있습니다.",
  authored_repair: "슬라이드를 복구하고 있습니다.",
  authored_export: "PowerPoint 파일을 만들고 있습니다.",
  authored_publication: "생성 결과를 게시하고 있습니다.",
};

export function generationStageMessage(stage?: string, current?: number, total?: number) {
  const label = stage ? GENERATION_STAGE_LABELS[stage] : undefined;
  if (!label) return undefined;
  return current !== undefined && total !== undefined && current <= total
    ? `${label} · ${current}/${total}장`
    : label;
}

/** The adapter admits public safe messages. Never render generic English errors. */
export function operationFailureMessage(operation: WorkspaceOperationAnchor): string {
  const message = operation.error?.trim();
  return message && /[가-힣]/.test(message) && !/traceback|stack trace|\bat \S+\([^)]*:\d+/i.test(message)
    ? message
    : operation.operationType === "presentation.generate"
      ? GENERATION_FAILURE_MESSAGE
      : "작업을 완료하지 못했어요. 작업 상세를 확인하거나 요청을 수정해 주세요.";
}

/** Same-state polls and heartbeat messages are not progress evidence. */
export function operationProgressEvidence(operation: WorkspaceOperationAnchor) {
  let version = -1;
  let signature: string | undefined;
  let lastProgressAt: string | undefined;
  const seen = new Set<string>();
  const messages: string[] = [];
  const completedStages = new Map<string, string>();
  for (const event of [...operation.events].sort((a, b) => a.sequence - b.sequence)) {
    if (event.operationId !== operation.operationId || seen.has(event.id)) continue;
    seen.add(event.id);
    if (event.operationVersion !== undefined && event.operationVersion < version) continue;
    version = event.operationVersion ?? version;
    if (!["operation.progress", "progress"].includes(event.type) || event.error) continue;
    const message = event.message?.trim();
    if (!event.stage && !message && event.current === undefined) continue;
    const next = JSON.stringify([event.stage, event.current, event.total, message]);
    if (next === signature) continue;
    signature = next;
    lastProgressAt = event.createdAt;
    const display = message && /[가-힣]/.test(message) ? message : generationStageMessage(event.stage, event.current, event.total);
    if (display && messages.at(-1) !== display) messages.push(display);
    if (display && event.stage && event.total !== undefined && event.current === event.total)
      completedStages.set(event.stage, display);
  }
  return { lastProgressAt, messages, completedStages: [...completedStages.values()] };
}
