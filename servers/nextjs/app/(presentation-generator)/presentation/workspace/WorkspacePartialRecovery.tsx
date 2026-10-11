"use client";

import { useEffect, useId, useRef, useState } from "react";
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
  const [plan, setPlan] = useState<WorkspacePartialRecoveryPlan>();
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [unknownAcknowledged, setUnknownAcknowledged] = useState(false);
  const [budgetAcknowledged, setBudgetAcknowledged] = useState(false);
  const [budgetPercent, setBudgetPercent] = useState(25);
  const [error, setError] = useState<string>();
  const [started, setStarted] = useState(false);
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
      if (!read.signal.aborted) setError(safeRequestError(reason, "현재 작업은 이어 만들기 계획을 확인할 수 없습니다."));
    } finally {
      if (!read.signal.aborted) setLoading(false);
    }
  };

  const confirm = async () => {
    if (!plan || disabled || submitting || !budgetAcknowledged || (plan.unknownIndices.length > 0 && !unknownAcknowledged)) return;
    const additionalBudgetMicrounits = Math.min(plan.additionalBudgetLimitMicrounits,
      Math.max(1, Math.floor(plan.additionalBudgetLimitMicrounits / 100 * budgetPercent)));
    if (!attempt.current) attempt.current = {
      idempotencyKey: globalThis.crypto?.randomUUID?.() ?? `partial-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      expectedRevision: plan.presentationRevision, expectedOperationVersion: plan.operationVersion,
      recoveryRevision: plan.recoveryRevision,
      recoveryDecision: { manifestHash: plan.manifestHash, nonce: plan.nonce,
        acknowledgedUnknownIndices: plan.unknownIndices.slice(), additionalBudgetMicrounits },
    };
    setSubmitting(true);
    setError(undefined);
    try {
      await onAction(operationId, "retry", attempt.current);
      attempt.current = undefined;
      setPlan(undefined);
      setStarted(true);
    } catch (reason) {
      const rejected = reason instanceof WorkspaceApiError && reason.status >= 400 && reason.status < 500
        && ![408, 429].includes(reason.status);
      if (rejected) {
        attempt.current = undefined;
        setPlan(undefined);
        setBudgetAcknowledged(false);
        setUnknownAcknowledged(false);
        setError("요청을 시작하지 못했습니다. 최신 계획을 다시 확인해 주세요.");
      } else {
        setError("전송 결과를 아직 확인하지 못했습니다. 같은 승인 요청으로 다시 확인할 수 있습니다.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  return <div className="mt-3 space-y-2" data-testid="workspace-partial-recovery">
    {!plan && <button type="button" disabled={disabled || loading || submitting} onClick={() => void loadPlan()}
      className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">
      {loading ? "이어 만들기 계획 확인 중…" : "남은 페이지 이어 만들기"}
    </button>}
    {plan && <section className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm" aria-label="남은 페이지 이어 만들기 계획">
      <h4 className="font-semibold text-slate-900">전체 {plan.expectedPageCount}페이지 이어 만들기</h4>
      <ul className="space-y-1 text-xs leading-5 text-slate-700">
        <li>기존 작성본 {plan.knownIndices.length}페이지 재사용 · 전체 품질 검수 전</li>
        <li>아직 시작하지 않은 {plan.newIndices.length}페이지 새로 생성</li>
        <li>결과 미확정 {plan.unknownIndices.length}페이지 · 다시 생성할 때 중복 사용 가능</li>
      </ul>
      {plan.unknownIndices.length > 0 && <label className="flex items-start gap-2 text-xs leading-5" htmlFor={`${identity}-unknown`}>
        <input id={`${identity}-unknown`} type="checkbox" className="mt-1" checked={unknownAcknowledged}
          disabled={disabled || submitting || !!attempt.current} onChange={(event) => setUnknownAcknowledged(event.target.checked)} />
        <span>{plan.unknownIndices.map((index) => index + 1).join(", ")}페이지의 이전 처리 결과를 확인할 수 없으며,
          다시 생성하면 사용량이 중복될 수 있음을 확인했습니다.</span>
      </label>}
      {plan.historicalActualCostUnbounded && <p className="text-xs leading-5 text-amber-800">이전 미확정 요청의 실제 사용량은 확정되지 않았습니다. 아래 한도는 추가 작업에만 적용됩니다.</p>}
      <label className="flex items-center justify-between gap-3 text-xs" htmlFor={`${identity}-budget`}>
        <span>추가 생성 한도</span>
        <select id={`${identity}-budget`} value={budgetPercent} disabled={disabled || submitting || !!attempt.current}
          onChange={(event) => { setBudgetPercent(Number(event.target.value)); setBudgetAcknowledged(false); }}
          className="rounded-md border border-slate-300 bg-white px-2 py-1">
          {[25, 50, 100].map((percent) => <option key={percent} value={percent}>허용 한도의 {percent}%</option>)}
        </select>
      </label>
      <p className="text-xs leading-5 text-slate-600">새 생성 요청 {plan.prospectiveAuthorRequestCount}회와
        수정 요청 최대 {plan.prospectiveRetryRequestLimit}회가 계획되어 있습니다. 선택한 한도에 도달하면 작업을 멈춥니다.</p>
      <label className="flex items-start gap-2 text-xs leading-5" htmlFor={`${identity}-budget-ack`}>
        <input id={`${identity}-budget-ack`} type="checkbox" className="mt-1" checked={budgetAcknowledged}
          disabled={disabled || submitting || !!attempt.current} onChange={(event) => setBudgetAcknowledged(event.target.checked)} />
        <span>허용 한도의 {budgetPercent}% 안에서 추가 생성을 진행하도록 승인합니다.</span>
      </label>
      <p className="text-xs text-slate-600">제작 후 전체 품질 검수와 내보내기를 확인해야 합니다.</p>
      <div className="flex gap-2">
        <button type="button" onClick={() => void confirm()}
          disabled={disabled || submitting || !budgetAcknowledged || (plan.unknownIndices.length > 0 && !unknownAcknowledged)}
          className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50">
          {submitting ? "승인 요청 확인 중…" : attempt.current ? "같은 승인 요청 결과 다시 확인" : "확인하고 이어 만들기"}
        </button>
        {!attempt.current && <button type="button" disabled={submitting} onClick={() => setPlan(undefined)}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs">닫기</button>}
      </div>
    </section>}
    {error && <p role="alert" className="text-xs leading-5 text-red-700">{error}</p>}
    {started && <p role="status" className="text-xs leading-5 text-slate-700">남은 페이지 제작을 요청했습니다. 전체 품질 검수와 내보내기가 남아 있습니다.</p>}
  </div>;
}
