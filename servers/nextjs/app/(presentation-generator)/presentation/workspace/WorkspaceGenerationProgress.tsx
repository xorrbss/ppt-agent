import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import type { WorkspaceOperationAnchor } from "./types";
import { APPROVABLE, TERMINAL } from "./WorkspaceOperationLabels";
import { formatTimestamp } from "./WorkspaceOperationResult";
import {
  operationProgressEvidence,
  generationStageMessage,
  type WorkspaceOperationObservation,
} from "./WorkspaceOperationPresentation";
import {
  buildGenerationProgress,
  etaMinutes,
  latestGenerationEventEvidence,
  type GenerationStep,
} from "./generationProgressSteps";
import { useGenerationActivity } from "../../hooks/useGenerationActivity";

const COUNTED_STEPS = new Set(["author", "render", "persist"]);

function StepMarker({ state, label }: { state: GenerationStep["state"]; label: string }) {
  if (state === "done") {
    return <span role="img" aria-label={label} className="w-4 shrink-0 text-center text-emerald-600">✓</span>;
  }
  if (state === "active") {
    return (
      <span role="img" aria-label={label} className="flex w-4 shrink-0 items-center justify-center">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-blue-200 border-t-blue-600 motion-reduce:animate-none" />
      </span>
    );
  }
  return <span role="img" aria-label={label} className="w-4 shrink-0 text-center text-slate-300">○</span>;
}

export function WorkspaceGenerationProgress({ operation, observation, locale }: {
  operation: WorkspaceOperationAnchor;
  observation?: WorkspaceOperationObservation;
  locale: string;
}) {
  const t = useTranslations("presentationGenerationProgress");
  const evidence = operationProgressEvidence(operation);
  const active = !TERMINAL.has(operation.status);
  const running = active && !APPROVABLE.has(operation.status) &&
    !["queued", "waiting_for_assets", "blocked", "cancelling"].includes(operation.status);
  const progressAt = observation?.lastProgressAt ?? evidence.lastProgressAt;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [running, operation.operationId, progressAt]);
  const snapshot = useGenerationActivity({ fast: running });
  const activityItem = running
    ? snapshot?.items.find((item) => item.operationId === operation.operationId) ?? null
    : null;
  const eventEvidence = latestGenerationEventEvidence(operation.events);
  const model = buildGenerationProgress({
    activity: activityItem,
    evidence: eventEvidence,
    now,
  });
  const progressMs = progressAt ? Date.parse(progressAt) : NaN;
  const confirmedMs = observation?.lastConfirmedAt ? Date.parse(observation.lastConfirmedAt) : NaN;
  const stale = running && Number.isFinite(progressMs) && now - progressMs >= 60_000;
  const checking = running && (observation?.connection === "checking" ||
    (stale && (!Number.isFinite(confirmedMs) || now - confirmedMs >= 60_000)));
  const current = evidence.messages.at(-1) ?? generationStageMessage(operation.stage, operation.current, operation.total);

  const elapsedMinutes = model.elapsedMs === null ? null : Math.floor(model.elapsedMs / 60_000);
  const header: string[] = [t("headerRunning")];
  if (elapsedMinutes !== null) {
    header.push(elapsedMinutes < 1 ? t("elapsedUnderMinute") : elapsedMinutes >= 60
      ? t("elapsedHours", { hours: Math.floor(elapsedMinutes / 60), minutes: elapsedMinutes % 60 })
      : t("elapsedMinutes", { minutes: elapsedMinutes }));
  }
  if (model.etaMs !== null) {
    const minutes = etaMinutes(model.etaMs);
    header.push(minutes === null ? t("etaSoon") : t("etaMinutes", { minutes }));
  }
  const idleSeconds = model.lastActivityAt === null ? null : Math.max(0, Math.floor((now - model.lastActivityAt) / 1000));
  const idleLabel = idleSeconds === null ? null : idleSeconds < 60
    ? t("lastActivitySeconds", { seconds: idleSeconds })
    : idleSeconds < 3600
      ? t("lastActivityMinutes", { minutes: Math.floor(idleSeconds / 60) })
      : t("lastActivityHours", { hours: Math.floor(idleSeconds / 3600) });

  return (
    <div className="mt-3 space-y-2 text-[15px] leading-relaxed" data-testid="generation-progress-response">
      {running ? (
        <div className="space-y-2" data-testid="generation-progress-checklist">
          <p className="text-sm font-medium text-slate-800">{header.join(" · ")}</p>
          <ul className="space-y-1.5" aria-label={t("checklistLabel")}>
            {model.steps.map((step) => {
              const counted = COUNTED_STEPS.has(step.id) && step.total !== undefined && step.current !== undefined;
              return (
                <li key={step.id} data-step={step.id} data-state={step.state} className="flex items-center gap-2 text-sm">
                  <StepMarker state={step.state} label={t(`state_${step.state}`)} />
                  <span className={`w-28 shrink-0 ${step.state === "pending" ? "text-slate-400" : step.state === "done" ? "text-slate-600" : "font-medium text-slate-900"}`}>
                    {t(`step_${step.id}`)}
                  </span>
                  {counted && step.state !== "pending" && (
                    <>
                      <span
                        role="progressbar"
                        aria-label={t(`step_${step.id}`)}
                        aria-valuemin={0}
                        aria-valuemax={step.total}
                        aria-valuenow={step.current}
                        aria-valuetext={t("counterValueText", { current: step.current!, total: step.total! })}
                        className="h-1.5 min-w-12 flex-1 overflow-hidden rounded-full bg-slate-200"
                      >
                        <span
                          className="block h-full rounded-full bg-blue-500 transition-[width] duration-500 motion-reduce:transition-none"
                          style={{ width: `${step.total ? Math.min(100, (step.current! / step.total!) * 100) : 0}%` }}
                        />
                      </span>
                      <span className="shrink-0 text-xs tabular-nums text-slate-600">{step.current}/{step.total}</span>
                    </>
                  )}
                </li>
              );
            })}
          </ul>
          {idleLabel && <p className="text-xs text-slate-500" data-testid="generation-last-activity">{idleLabel}</p>}
        </div>
      ) : (
        evidence.completedStages.filter((message) => message !== current).map((message) => (
          <p key={message} className="flex items-start gap-2 text-sm text-slate-600"><span aria-label={t("completed")}>✓</span>{message}</p>
        ))
      )}
      {running && <p className="flex items-start gap-2" role="status">
        <span aria-hidden="true" className={`mt-2 h-2 w-2 shrink-0 rounded-full bg-slate-500 ${checking ? "" : "animate-pulse motion-reduce:animate-none"}`} />
        <span>{checking ? t("checkingConnection") : current ?? t("processingRequest")}</span>
      </p>}
      {(stale || checking) && <div className="space-y-1 text-xs text-slate-600" data-testid="generation-observation">
        {stale && !checking && <p role="status">{t("noRecentProgress")}</p>}
        <p>{t("lastServerCheck", { time: formatTimestamp(observation?.lastConfirmedAt, locale) ?? t("noInformation") })}</p>
        <p data-testid="generation-last-progress-at">{t("lastProgress", { time: formatTimestamp(progressAt, locale) ?? t("noInformation") })}</p>
      </div>}
    </div>
  );
}
