import { useEffect, useState } from "react";
import type { WorkspaceOperationAnchor } from "./types";
import { APPROVABLE, TERMINAL } from "./WorkspaceOperationLabels";
import { formatTimestamp } from "./WorkspaceOperationResult";
import {
  operationProgressEvidence,
  generationStageMessage,
  type WorkspaceOperationObservation,
} from "./WorkspaceOperationPresentation";

export function WorkspaceGenerationProgress({ operation, observation, locale }: {
  operation: WorkspaceOperationAnchor;
  observation?: WorkspaceOperationObservation;
  locale: string;
}) {
  const evidence = operationProgressEvidence(operation);
  const active = !TERMINAL.has(operation.status);
  const running = active && !APPROVABLE.has(operation.status) &&
    !["queued", "waiting_for_assets", "blocked", "cancelling"].includes(operation.status);
  const progressAt = observation?.lastProgressAt ?? evidence.lastProgressAt;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, [running, operation.operationId, progressAt]);
  const progressMs = progressAt ? Date.parse(progressAt) : NaN;
  const confirmedMs = observation?.lastConfirmedAt ? Date.parse(observation.lastConfirmedAt) : NaN;
  const stale = running && Number.isFinite(progressMs) && now - progressMs >= 60_000;
  const checking = running && (observation?.connection === "checking" ||
    (stale && (!Number.isFinite(confirmedMs) || now - confirmedMs >= 60_000)));
  const current = evidence.messages.at(-1) ?? generationStageMessage(operation.stage, operation.current, operation.total);
  return (
    <div className="mt-3 space-y-2 text-[15px] leading-relaxed" data-testid="generation-progress-response">
      {evidence.completedStages.filter((message) => message !== current).map((message) => (
        <p key={message} className="flex items-start gap-2 text-sm text-slate-600"><span aria-label="완료">✓</span>{message}</p>
      ))}
      {running && <p className="flex items-start gap-2" role="status">
        <span aria-hidden="true" className={`mt-2 h-2 w-2 shrink-0 rounded-full bg-slate-500 ${checking ? "" : "animate-pulse motion-reduce:animate-none"}`} />
        <span>{checking ? "연결을 확인하고 있어요" : current ?? "프레젠테이션 생성 요청을 처리하고 있습니다."}</span>
      </p>}
      {(stale || checking) && <div className="space-y-1 text-xs text-slate-600" data-testid="generation-observation">
        {stale && !checking && <p role="status">서버에서 실행 상태를 확인했어요. 진행 변화는 아직 없습니다.</p>}
        <p>마지막 서버 확인 · {formatTimestamp(observation?.lastConfirmedAt, locale) ?? "확인 정보 없음"}</p>
        <p data-testid="generation-last-progress-at">마지막 진행 변화 · {formatTimestamp(progressAt, locale) ?? "확인 정보 없음"}</p>
      </div>}
    </div>
  );
}
