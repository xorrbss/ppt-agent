"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { getWorkspacePartialRecoveryPlan, WorkspaceApiError } from "../../services/api/workspace";
import type { WorkspaceOperationActionOptions, WorkspacePartialRecoveryPlan } from "./types";
import { safeRequestError } from "./WorkspaceOperationResult";

interface Props {
  operationId: string;
  operationVersion?: number;
  currentRevision?: number;
  disabled?: boolean;
  onAction: (operationId: string, action: "retry", options: WorkspaceOperationActionOptions) => void | Promise<void>;
}

export function WorkspacePartialRecovery({ operationId, operationVersion, currentRevision, disabled = false, onAction }: Props) {
  const t = useTranslations("generationFailure.recovery");
  const [plan, setPlan] = useState<WorkspacePartialRecoveryPlan>();
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [unknownAcknowledged, setUnknownAcknowledged] = useState(false);
  const [budgetAcknowledged, setBudgetAcknowledged] = useState(false);
  const [budgetPercent, setBudgetPercent] = useState(25);
  const [error, setError] = useState<string>();
  const [started, setStarted] = useState(false);
  // Render-visible mirror of attempt.current: the ref stays the synchronous guard for handlers.
  const [attemptPending, setAttemptPending] = useState(false);
  const attempt = useRef<WorkspaceOperationActionOptions | undefined>(undefined);
  const controller = useRef<AbortController | undefined>(undefined);
  const identity = useId();

  useEffect(() => {
    controller.current?.abort();
    setLoading(false);
    if (!attempt.current) {
      setPlan(undefined);
      setBudgetAcknowledged(false);
      setUnknownAcknowledged(false);
    }
    return () => controller.current?.abort();
  }, [operationId, operationVersion, currentRevision]);

  const loadPlan = async () => {
    if (disabled || loading || submitting || attempt.current) return;
    controller.current?.abort();
    const read = new AbortController();
    controller.current = read;
    setLoading(true);
    setError(undefined);
    setStarted(false);
    setBudgetAcknowledged(false);
    setUnknownAcknowledged(false);
    try {
      const next = await getWorkspacePartialRecoveryPlan(operationId, read.signal);
      if (!read.signal.aborted) setPlan(next);
    } catch (reason) {
      if (!read.signal.aborted) setError(safeRequestError(reason, t("planUnavailable")));
    } finally {
      if (!read.signal.aborted) setLoading(false);
    }
  };

  const confirm = async () => {
    if (!plan || disabled || submitting || !budgetAcknowledged || (plan.unknownIndices.length > 0 && !unknownAcknowledged)) return;
    const additionalBudgetMicrounits = Math.min(plan.additionalBudgetLimitMicrounits,
      Math.max(1, Math.floor(plan.additionalBudgetLimitMicrounits / 100 * budgetPercent)));
    if (!attempt.current) {
      setAttemptPending(true);
      attempt.current = {
        idempotencyKey: globalThis.crypto?.randomUUID?.() ?? `partial-${Date.now()}-${Math.random().toString(36).slice(2)}`,
        expectedRevision: plan.presentationRevision, expectedOperationVersion: plan.operationVersion,
        recoveryRevision: plan.recoveryRevision,
        recoveryDecision: { manifestHash: plan.manifestHash, nonce: plan.nonce,
          acknowledgedUnknownIndices: plan.unknownIndices.slice(), additionalBudgetMicrounits },
      };
    }
    setSubmitting(true);
    setError(undefined);
    try {
      await onAction(operationId, "retry", attempt.current);
      attempt.current = undefined;
      setAttemptPending(false);
      setPlan(undefined);
      setStarted(true);
    } catch (reason) {
      const rejected = reason instanceof WorkspaceApiError && reason.status >= 400 && reason.status < 500
        && ![408, 429].includes(reason.status);
      if (rejected) {
        attempt.current = undefined;
        setAttemptPending(false);
        setPlan(undefined);
        setBudgetAcknowledged(false);
        setUnknownAcknowledged(false);
        setError(t("rejected"));
      } else {
        setError(t("outcomeUnknown"));
      }
    } finally {
      setSubmitting(false);
    }
  };

  return <div className="mt-3 space-y-2" data-testid="workspace-partial-recovery">
    {!plan && <button type="button" disabled={disabled || loading || submitting} onClick={() => void loadPlan()}
      className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
      {loading ? t("checking") : t("start")}
    </button>}
    {plan && <section className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm" aria-label={t("planAria")}>
      <h4 className="font-semibold text-slate-900">{t("planTitle", { count: plan.expectedPageCount })}</h4>
      <ul className="space-y-1 text-xs leading-5 text-slate-700">
        <li>{t("reuse", { count: plan.knownIndices.length })}</li>
        <li>{t("create", { count: plan.newIndices.length })}</li>
        <li>{t("unknownResult", { count: plan.unknownIndices.length })}</li>
      </ul>
      {plan.unknownIndices.length > 0 && <label className="flex items-start gap-2 text-xs leading-5" htmlFor={`${identity}-unknown`}>
        <input id={`${identity}-unknown`} type="checkbox" className="mt-1" checked={unknownAcknowledged}
          disabled={disabled || submitting || attemptPending} onChange={(event) => setUnknownAcknowledged(event.target.checked)} />
        <span>{t("ackUnknown", { pages: plan.unknownIndices.map((index) => index + 1).join(", ") })}</span>
      </label>}
      {plan.historicalActualCostUnbounded && <p className="text-xs leading-5 text-amber-800">{t("historicalUnbounded")}</p>}
      <label className="flex items-center justify-between gap-3 text-xs" htmlFor={`${identity}-budget`}>
        <span>{t("budgetLabel")}</span>
        <select id={`${identity}-budget`} value={budgetPercent} disabled={disabled || submitting || attemptPending}
          onChange={(event) => { setBudgetPercent(Number(event.target.value)); setBudgetAcknowledged(false); }}
          className="rounded-md border border-slate-300 bg-white px-2 py-1">
          {[25, 50, 100].map((percent) => <option key={percent} value={percent}>{t("budgetOption", { percent })}</option>)}
        </select>
      </label>
      <p className="text-xs leading-5 text-slate-600">{t("requestsPlanned", {
        authorCount: plan.prospectiveAuthorRequestCount, retryLimit: plan.prospectiveRetryRequestLimit })}</p>
      <label className="flex items-start gap-2 text-xs leading-5" htmlFor={`${identity}-budget-ack`}>
        <input id={`${identity}-budget-ack`} type="checkbox" className="mt-1" checked={budgetAcknowledged}
          disabled={disabled || submitting || attemptPending} onChange={(event) => setBudgetAcknowledged(event.target.checked)} />
        <span>{t("ackBudget", { percent: budgetPercent })}</span>
      </label>
      <p className="text-xs text-slate-600">{t("afterNote")}</p>
      <div className="flex gap-2">
        <button type="button" onClick={() => void confirm()}
          disabled={disabled || submitting || !budgetAcknowledged || (plan.unknownIndices.length > 0 && !unknownAcknowledged)}
          className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">
          {submitting ? t("confirming") : attemptPending ? t("confirmAgain") : t("confirm")}
        </button>
        {!attemptPending && <button type="button" disabled={submitting} onClick={() => setPlan(undefined)}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs">{t("close")}</button>}
      </div>
    </section>}
    {error && <p role="alert" className="text-xs leading-5 text-red-700">{error}</p>}
    {started && <p role="status" className="text-xs leading-5 text-slate-700">{t("started")}</p>}
  </div>;
}
