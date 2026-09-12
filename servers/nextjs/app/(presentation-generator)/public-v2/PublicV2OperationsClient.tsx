"use client";

import { FormEvent, useMemo, useState } from "react";

import {
  type PublicV2DesignPackPin,
  type PublicV2GenerationMode,
  type PublicV2OperationSnapshot,
  usePublicV2Operation,
} from "@/lib/public-v2";

type ExportFormat = "pptx" | "pdf";

type DisplayArtifact = {
  artifactId: string;
  kind: string;
  download: string;
  sizeBytes?: number;
};

type DisplayCheck = {
  label: string;
  status: string;
  reasonCode?: string;
  required?: boolean;
};

type DisplaySnapshot = {
  phase?: string;
  status?: string;
  operationId?: string;
  presentationId?: string;
  progressPercent?: number;
  operationPhase?: string;
  sourceRevision?: number;
  failure?: { reasonCode?: string; retryable?: boolean } | null;
  quality?: {
    releaseReadiness?: string;
    sourceRevision?: number;
    checks?: Record<string, { status?: string; reasonCode?: string; required?: boolean }>;
  } | null;
  generationRouteReceipt: PublicV2OperationSnapshot["generationRouteReceipt"];
  readiness: PublicV2OperationSnapshot["readiness"];
  generationTiming: PublicV2OperationSnapshot["generationTiming"];
  artifacts?: DisplayArtifact[];
  downloadStatus?: string;
  canCancel?: boolean;
  pendingAdmission?: boolean;
};

const EMPTY_FORM = {
  content: "",
  instructions: "",
  slideCount: "",
  generationMode: "auto" as PublicV2GenerationMode,
  designPackId: "",
  designPackVersion: "",
  designPackDigest: "",
};

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;

const GENERATION_MODE_LABELS: Record<PublicV2GenerationMode, string> = {
  auto: "자동 선택",
  "registered-template": "등록 템플릿 그대로",
  "brand-style": "브랜드 스타일 적용",
  "free-design": "자유 디자인",
  "fidelity-first": "Fidelity 우선",
  "editable-first": "편집 가능성 우선",
};

function receiptValue(
  receipt: Record<string, unknown> | null,
  field: string,
): unknown {
  return receipt && Object.prototype.hasOwnProperty.call(receipt, field)
    ? receipt[field]
    : undefined;
}

function displaySnapshot(snapshot: PublicV2OperationSnapshot): DisplaySnapshot {
  const receipt = snapshot.quality.receipt;
  const checks = Object.fromEntries(
    [
      "contentBrief",
      "authoring",
      "geometryFont",
      "imageContentIntegrity",
      "visualReview",
      "performance",
      "nativeCoverageOoxml",
    ].flatMap((label) => {
      const value = receiptValue(receipt, label);
      if (!value || typeof value !== "object" || Array.isArray(value)) return [];
      const check = value as Record<string, unknown>;
      return [[label, {
        ...(typeof check.status === "string" ? { status: check.status } : {}),
        ...(typeof check.reasonCode === "string" ? { reasonCode: check.reasonCode } : {}),
        ...(typeof check.required === "boolean" ? { required: check.required } : {}),
      }]];
    }),
  );
  const sourceRevision = receiptValue(receipt, "sourceRevision");
  return {
    phase: snapshot.lifecycle,
    status: snapshot.status ?? undefined,
    operationId: snapshot.operationId ?? undefined,
    presentationId: snapshot.presentationId ?? undefined,
    progressPercent: snapshot.progressPercent ?? undefined,
    operationPhase: snapshot.phase ?? undefined,
    ...(typeof snapshot.sourceRevision === "number"
      ? { sourceRevision: snapshot.sourceRevision }
      : {}),
    failure: snapshot.failure,
    quality: {
      releaseReadiness: snapshot.quality.releaseReadiness,
      ...(typeof sourceRevision === "number" ? { sourceRevision } : {}),
      checks,
    },
    generationRouteReceipt: snapshot.generationRouteReceipt,
    readiness: snapshot.readiness,
    generationTiming: snapshot.generationTiming,
    artifacts: [...snapshot.artifacts],
    downloadStatus: snapshot.downloadStatus,
    canCancel: snapshot.canCancel,
    pendingAdmission: snapshot.pendingAdmission,
  };
}

function formatBytes(value?: number): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (value < 1024) return `${value} B`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatMillis(value: number | "not_started"): string {
  return value === "not_started" ? "시작 안 됨" : `${value.toLocaleString()} ms`;
}

function formatCounter(value: number | "not_started"): string {
  return value === "not_started" ? "시작 안 됨" : value.toLocaleString();
}

const TIMING_STAGE_LABELS = {
  outline: "Outline",
  provider: "Provider",
  authoring: "Authoring",
  repair: "Repair",
  chromiumReview: "Chromium review",
  publication: "Publication",
} as const;

function statusTone(status?: string): string {
  if (status === "succeeded" || status === "ready") return "border-emerald-200 bg-emerald-50 text-emerald-900";
  if (status === "failed" || status === "blocked") return "border-red-200 bg-red-50 text-red-900";
  if (status === "cancelled") return "border-slate-300 bg-slate-100 text-slate-800";
  return "border-blue-200 bg-blue-50 text-blue-900";
}

function readableStatus(
  status?: string,
  releaseReadiness?: string,
): string {
  if (status === "succeeded" && releaseReadiness !== "ready") {
    return "릴리스 검토 미완료";
  }
  switch (status) {
    case "queued": return "대기 중";
    case "running": return "처리 중";
    case "succeeded": return "완료";
    case "failed": return "실패";
    case "cancelled": return "취소됨";
    case "ready": return "준비 완료";
    case "blocked": return "차단됨";
    case "not_assessed": return "평가 안 됨";
    default: return status ?? "아직 작업 없음";
  }
}

function qualityChecks(quality: DisplaySnapshot["quality"]): DisplayCheck[] {
  if (!quality?.checks) return [];
  return Object.entries(quality.checks).map(([label, check]) => ({
    label,
    status: check.status ?? "not_assessed",
    reasonCode: check.reasonCode,
    required: check.required,
  }));
}

function requestFailure(error: unknown): string {
  const reasonCode = error && typeof error === "object" && "reasonCode" in error
    ? (error as { reasonCode?: unknown }).reasonCode
    : error;
  switch (reasonCode) {
    case "generation_content_required": return "프레젠테이션 내용을 입력하세요.";
    case "public_v2_slide_count_must_be_between_1_and_100": return "슬라이드 수는 1~100 사이로 입력하세요.";
    case "public_v2_registered_template_pin_required": return "등록 템플릿 정보를 확인하세요.";
    case "public_v2_source_revision_unavailable": return "최신 작업 상태를 확인한 뒤 다시 시도하세요.";
    case "public_v2_network_error": return "서버에 연결하지 못했습니다. 연결 상태를 확인하세요.";
    default: return "요청을 완료하지 못했습니다. 잠시 후 다시 시도하세요.";
  }
}

export default function PublicV2OperationsClient({ storageScope }: { storageScope: string }) {
  const {
    snapshot,
    submitGeneration: submitGenerationRequest,
    submitExport,
    recoverPendingExport,
    cancel,
    clear,
  } = usePublicV2Operation({ storageScope });
  const [form, setForm] = useState(EMPTY_FORM);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [requestedDownloadId, setRequestedDownloadId] = useState<string | null>(null);

  const current = displaySnapshot(snapshot);
  const checks = useMemo(() => qualityChecks(current.quality), [current.quality]);
  const terminal = current.phase === "succeeded" || current.phase === "failed" || current.phase === "cancelled";
  const isCancelled = current.phase === "cancelled" || current.status === "cancelled";
  const isFailed = current.phase === "failed" || current.status === "failed";
  const hasOperation = Boolean(current.operationId && current.presentationId);
  const releaseReviewIncomplete = current.quality?.releaseReadiness !== "ready";
  const pendingExport = snapshot.pendingAdmission && snapshot.kind === "presentation.export";
  const hasPptxArtifact = current.artifacts?.some((artifact) => artifact.kind === "presentation-pptx") ?? false;
  const hasPdfArtifact = current.artifacts?.some((artifact) => artifact.kind === "presentation-pdf") ?? false;
  const canPrepareAnotherFidelity = current.status === "succeeded" && current.presentationId && (!hasPptxArtifact || !hasPdfArtifact);

  const submitGeneration = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = form.content.trim();
    if (!content) {
      setRequestError(requestFailure("generation_content_required"));
      return;
    }
    const requestedSlideCount = form.slideCount ? Number(form.slideCount) : undefined;
    if (
      requestedSlideCount !== undefined &&
      (!Number.isSafeInteger(requestedSlideCount) ||
        requestedSlideCount < 1 ||
        requestedSlideCount > 100)
    ) {
      setRequestError(requestFailure("public_v2_slide_count_must_be_between_1_and_100"));
      return;
    }
    let designPackPin: PublicV2DesignPackPin | undefined;
    if (form.generationMode === "registered-template") {
      const designPackId = form.designPackId.trim();
      const version = Number(form.designPackVersion);
      const digest = form.designPackDigest.trim();
      if (
        !designPackId
        || !Number.isSafeInteger(version)
        || version < 1
        || !SHA256_DIGEST.test(digest)
      ) {
        setRequestError(requestFailure("public_v2_registered_template_pin_required"));
        return;
      }
      designPackPin = { designPackId, version, digest };
    }
    setRequestError(null);
    setRequestedDownloadId(null);
    try {
      await submitGenerationRequest({
        content,
        ...(form.instructions.trim() ? { instructions: form.instructions.trim() } : {}),
        ...(requestedSlideCount === undefined ? {} : { slideCount: requestedSlideCount }),
        generationMode: form.generationMode,
        ...(designPackPin ? { designPackPin } : {}),
        qualityPolicy: "interactive-v1",
      });
    } catch (error) {
      setRequestError(requestFailure(error));
    }
  };

  const requestExport = async (format: ExportFormat) => {
    if (!current.presentationId) return;
    const sourceRevision = current.sourceRevision ?? current.quality?.sourceRevision;
    if (!Number.isSafeInteger(sourceRevision) || (sourceRevision ?? -1) < 0) {
      setRequestError(requestFailure("public_v2_source_revision_unavailable"));
      return;
    }
    const confirmedSourceRevision = sourceRevision as number;
    setRequestError(null);
    setRequestedDownloadId(null);
    try {
      await submitExport(current.presentationId, {
        sourceRevision: confirmedSourceRevision,
        qualityPolicy: "release-v1",
        output: {
          format,
          mode: "fidelity-raster",
          fontEmbedding: "none",
        },
      });
    } catch (error) {
      setRequestError(requestFailure(error));
    }
  };

  const requestCancel = async () => {
    setRequestError(null);
    try {
      await cancel();
    } catch (error) {
      setRequestError(requestFailure(error));
    }
  };

  const abandonPendingAdmission = () => {
    clear();
    setRequestError(null);
    setRequestedDownloadId(null);
  };

  const recoverExportAdmission = async () => {
    setRequestError(null);
    try {
      await recoverPendingExport();
    } catch (error) {
      setRequestError(requestFailure(error));
    }
  };

  return (
    <main className="mx-auto min-h-screen w-full min-w-0 max-w-6xl bg-[#F8F9FB] px-4 py-6 text-slate-950 sm:px-6 sm:py-10">
      <header className="min-w-0 rounded-3xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_30px_-18px_rgba(15,23,42,0.18)] sm:p-7">
        <p className="inline-flex rounded-full bg-violet-50 px-3 py-1 text-[11px] font-semibold tracking-[0.12em] text-violet-600">PUBLIC V2</p>
        <h1 className="mt-4 text-2xl font-semibold leading-tight tracking-[-0.035em] sm:text-3xl">프레젠테이션 생성과 Fidelity 다운로드</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
          생성은 development 초안으로, 파일 준비는 release 품질 프로필로 분리됩니다. 브라우저를 새로 열어도 같은 idempotency 키와 이벤트 cursor로 이어집니다.
        </p>
      </header>

      <div className="mt-6 grid min-w-0 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <section className="min-w-0 rounded-3xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_30px_-18px_rgba(15,23,42,0.18)] sm:p-7" aria-labelledby="generation-title">
            <h2 id="generation-title" className="text-lg font-bold">새 프레젠테이션</h2>
            <form className="mt-5 space-y-5" onSubmit={submitGeneration}>
              {current.pendingAdmission && (
                <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950" role="status">
                  <p className="font-semibold">접수 결과를 복구해야 합니다.</p>
                  <p className="mt-1 leading-5">{pendingExport ? "이전 파일 준비 요청의 응답을 저장하기 전에 연결이 끊겼습니다. 저장된 비민감 export descriptor와 idempotency 키로 기존 접수를 다시 확인할 수 있습니다." : "이전 생성 요청의 응답을 저장하기 전에 연결이 끊겼습니다. 같은 내용과 품질 정책으로 다시 제출하면 저장된 idempotency 키로 기존 접수를 확인합니다."}</p>
                  {pendingExport && <button type="button" disabled={current.phase === "submitting"} onClick={() => void recoverExportAdmission()} className="mt-2 mr-2 rounded-lg bg-amber-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-800 disabled:cursor-not-allowed disabled:opacity-60">파일 준비 접수 복구</button>}
                  <button type="button" disabled={current.phase === "submitting"} onClick={abandonPendingAdmission} className="mt-2 rounded-lg border border-amber-500 px-3 py-1.5 text-xs font-semibold hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60">이 접수를 포기하고 새 요청 시작</button>
                </div>
              )}
            <label className="block min-w-0 text-sm font-medium text-slate-700" htmlFor="public-v2-content">
              내용
              <textarea id="public-v2-content" required value={form.content} onChange={(event) => setForm((value) => ({ ...value, content: event.target.value }))} className="mt-2 min-h-44 w-full resize-y rounded-2xl border border-slate-200 bg-slate-50/50 px-4 py-3 text-base leading-7 outline-none transition-colors placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/60" placeholder="만들 프레젠테이션의 목적과 핵심 내용을 입력하세요." />
            </label>
            <label className="block min-w-0 text-sm font-medium text-slate-700" htmlFor="public-v2-instructions">
              추가 지시 <span className="font-normal text-slate-500">(선택)</span>
              <input id="public-v2-instructions" value={form.instructions} onChange={(event) => setForm((value) => ({ ...value, instructions: event.target.value }))} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-slate-400 focus:border-violet-300 focus:ring-4 focus:ring-violet-100/60" placeholder="대상, 톤, 강조할 항목" />
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block min-w-0 text-sm font-medium text-slate-700" htmlFor="public-v2-generation-mode">
                디자인 방식 <span className="font-normal text-slate-500">(문서마다 새로 선택)</span>
                <select id="public-v2-generation-mode" value={form.generationMode} onChange={(event) => setForm((value) => ({ ...value, generationMode: event.target.value as PublicV2GenerationMode }))} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-slate-400 focus:border-violet-300 focus:ring-4 focus:ring-violet-100/60">
                  <option value="auto">자동 선택</option>
                  <option value="registered-template">등록 템플릿 그대로</option>
                  <option value="brand-style">브랜드 스타일 적용</option>
                  <option value="free-design">자유 디자인</option>
                  <option value="fidelity-first">Fidelity 우선</option>
                  <option value="editable-first" disabled>편집 가능성 우선 (현재 지원 안 됨)</option>
                </select>
                <span className="mt-1 block text-xs font-normal text-slate-500">편집 가능성 우선은 Public v2의 Fidelity-only 출력 계약에서 사용할 수 없습니다.</span>
              </label>
              <label className="block min-w-0 text-sm font-medium text-slate-700" htmlFor="public-v2-slide-count">
                슬라이드 수 <span className="font-normal text-slate-500">(선택)</span>
                <input id="public-v2-slide-count" min={1} max={100} inputMode="numeric" type="number" value={form.slideCount} onChange={(event) => setForm((value) => ({ ...value, slideCount: event.target.value }))} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-slate-400 focus:border-violet-300 focus:ring-4 focus:ring-violet-100/60" />
              </label>
            </div>
            {form.generationMode === "registered-template" && <fieldset className="rounded-xl border border-slate-200 bg-slate-50 p-4"><legend className="px-1 text-sm font-semibold text-slate-800">등록 템플릿 고정 정보</legend><div className="grid min-w-0 gap-3 sm:grid-cols-3"><label className="text-xs font-medium text-slate-700" htmlFor="public-v2-design-pack-id">Design Pack ID<input id="public-v2-design-pack-id" required value={form.designPackId} onChange={(event) => setForm((value) => ({ ...value, designPackId: event.target.value }))} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" /></label><label className="text-xs font-medium text-slate-700" htmlFor="public-v2-design-pack-version">버전<input id="public-v2-design-pack-version" required min={1} inputMode="numeric" type="number" value={form.designPackVersion} onChange={(event) => setForm((value) => ({ ...value, designPackVersion: event.target.value }))} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" /></label><label className="text-xs font-medium text-slate-700" htmlFor="public-v2-design-pack-digest">Digest<input id="public-v2-design-pack-digest" required pattern="sha256:[0-9a-f]{64}" value={form.designPackDigest} onChange={(event) => setForm((value) => ({ ...value, designPackDigest: event.target.value }))} placeholder="sha256:…" className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2 font-mono text-sm" /></label></div></fieldset>}
            <button type="submit" disabled={pendingExport || current.phase === "submitting" || current.phase === "monitoring"} className="inline-flex min-h-11 w-full items-center justify-center rounded-xl bg-slate-900 px-5 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-violet-200 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto">
              {pendingExport ? "파일 접수 복구 필요" : current.pendingAdmission ? "접수 복구 재시도" : "생성 시작"}
            </button>
          </form>
        </section>

        <aside className="min-w-0 rounded-2xl border border-slate-200/80 bg-slate-100/70 p-5" aria-label="Fidelity 지원 범위">
          <h2 className="font-semibold text-slate-800">Fidelity 출력만 제공</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
             PPTX와 PDF는 1280×720 화면을 raster로 보존합니다. 슬라이드 안의 개체와 접근 가능한 텍스트는 개별 편집할 수 없습니다.
          </p>
          <p className="mt-3 text-xs leading-5 text-slate-500">
             CJK 글자 크기·내용 밀도, overflow, occlusion은 release receipt가 통과해야 최종 완료로 표시됩니다. 이 경로에서는 Editable Hybrid를 지원하거나 노출하지 않습니다.
          </p>
        </aside>
      </div>

      <section className="mt-6 min-w-0 rounded-3xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_30px_-18px_rgba(15,23,42,0.18)] sm:p-7" aria-live="polite" aria-labelledby="operation-title">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="operation-title" className="text-lg font-bold">작업 상태</h2>
            {current.operationId && <p className="mt-1 break-all font-mono text-xs text-slate-500">operationId: {current.operationId}</p>}
          </div>
          <span className={`max-w-full rounded-full border px-3 py-1.5 text-xs font-semibold ${statusTone(current.status ?? current.phase)}`}>{readableStatus(current.status ?? current.phase, current.quality?.releaseReadiness)}</span>
        </div>

        {!hasOperation && <p className="mt-5 rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-5 py-8 text-sm leading-6 text-slate-500">생성을 시작하면 이곳에서 resume 가능한 작업과 품질 상태를 확인합니다.</p>}
        {hasOperation && (
          <div className="mt-5 space-y-5">
            <div className="rounded-xl bg-slate-50 p-4 text-sm text-slate-700">
              <div className="flex flex-wrap justify-between gap-2"><span>현재 단계: {current.operationPhase ?? "상태 확인 중"}</span><span>{typeof current.progressPercent === "number" ? `${current.progressPercent}%` : "진행률 확인 중"}</span></div>
              {typeof current.progressPercent === "number" && <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-violet-500 transition-all motion-reduce:transition-none" style={{ width: `${current.progressPercent}%` }} /></div>}
            </div>
            {!terminal && current.canCancel && <button type="button" onClick={() => void requestCancel()} className="rounded-lg border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50">이 작업 취소</button>}
            {isCancelled && <div className="rounded-xl border border-slate-300 bg-slate-50 p-4 text-sm text-slate-800" role="status"><p className="font-semibold">작업이 취소되었습니다.</p><p className="mt-1">{current.failure?.reasonCode ? "취소 요청이 처리되었습니다." : "생성 실패와 별개로 취소 요청이 처리되었습니다."}</p></div>}
            {isFailed && <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900" role="alert"><p className="font-semibold">작업이 실패했습니다.</p><p className="mt-1">실패 사유: {requestFailure(current.failure)}</p><p className="mt-1 text-xs">{current.failure?.retryable ? "이 오류는 재시도할 수 있습니다." : "새 요청을 만들기 전에 입력과 품질 상태를 확인하세요."}</p></div>}
            {current.generationRouteReceipt && <section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="서버 실행 경로 영수증"><h3 className="font-semibold text-slate-900">선택된 생성 방식</h3><dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2"><div><dt className="text-slate-500">요청 방식</dt><dd>{GENERATION_MODE_LABELS[current.generationRouteReceipt.requestedMode]}</dd></div><div><dt className="text-slate-500">서버 선택</dt><dd>{current.generationRouteReceipt.selectedRoute === "source-native" ? "등록 원본 구조 사용" : "자유 디자인 대체 경로"}</dd></div><div><dt className="text-slate-500">출력 역량</dt><dd>{current.generationRouteReceipt.outputCapability === "editable" ? "편집 가능" : "Fidelity"}</dd></div>{current.generationRouteReceipt.designPackPin && <div><dt className="text-slate-500">등록 템플릿</dt><dd>{current.generationRouteReceipt.designPackPin.designPackId} · v{current.generationRouteReceipt.designPackPin.version}</dd></div>}</dl>{current.generationRouteReceipt.fallbackReason && <p className="mt-2 text-xs text-amber-800">대체 경로 사유: {current.generationRouteReceipt.fallbackReason}</p>}</section>}
          </div>
        )}

        {current.quality && <section className={`mt-4 rounded-xl border p-4 ${statusTone(current.quality.releaseReadiness)}`} aria-label="품질 준비 상태"><h3 className="font-semibold">품질 준비 상태: {readableStatus(current.quality.releaseReadiness)}</h3>{current.quality.releaseReadiness !== "ready" && <p className="mt-1 text-sm">{current.quality.releaseReadiness === "not_assessed" ? "visual, performance, native coverage 검토가 아직 평가되지 않아 최종 완료 또는 릴리스 파일로 표시하지 않습니다." : "필수 품질 검토가 준비되지 않았거나 통과하지 못해 릴리스 준비 상태가 차단되었습니다."}</p>}{checks.length > 0 && <ul className="mt-3 space-y-2 text-sm">{checks.map((check) => <li key={check.label} className="rounded-lg bg-white/60 px-3 py-2"><span className="font-medium">{check.label}</span>: {readableStatus(check.status)}{check.required ? " · 필수" : ""}{check.reasonCode ? ` · ${check.reasonCode}` : ""}</li>)}</ul>}</section>}

        <section className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4" aria-label="독립 준비 상태">
          <h3 className="font-semibold text-slate-900">독립 준비 상태</h3>
          <p className="mt-1 text-xs text-slate-600">범위: {current.readiness.scope}. Fidelity 파일 준비는 승격·배포·네이티브 편집 가능성을 의미하지 않습니다.</p>
          <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
            <div className="rounded-lg bg-white px-3 py-2"><dt className="font-medium">Fidelity artifact</dt><dd>{current.readiness.fidelityArtifactReady ? "준비됨" : "준비 안 됨"}</dd></div>
            <div className="rounded-lg bg-white px-3 py-2"><dt className="font-medium">Native editability</dt><dd>{current.readiness.nativeEditabilityReady ? "준비됨" : "준비 안 됨"}</dd></div>
            <div className="rounded-lg bg-white px-3 py-2"><dt className="font-medium">Promotion</dt><dd>{current.readiness.promotionReady ? "준비됨" : "승격 안 됨"}</dd></div>
            <div className="rounded-lg bg-white px-3 py-2"><dt className="font-medium">Deployment authorization</dt><dd>{current.readiness.deploymentAuthorized ? "승인됨" : "승인 안 됨"}</dd></div>
          </dl>
        </section>

        {current.generationTiming && <section className="mt-4 rounded-xl border border-slate-200 bg-white p-4" aria-label="생성 단계 timing"><h3 className="font-semibold text-slate-900">생성 단계 timing</h3><p className="mt-1 text-xs text-slate-600">시도 {current.generationTiming.attemptNumber} · queue wait {formatMillis(current.generationTiming.queueWaitMillis)} · wall {formatMillis(current.generationTiming.wallMillis)} · logical calls {formatCounter(current.generationTiming.logicalCalls)}</p><dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">{Object.entries(TIMING_STAGE_LABELS).map(([stage, label]) => { const timing = current.generationTiming?.stages[stage as keyof typeof TIMING_STAGE_LABELS]; return <div key={stage} className="rounded-lg bg-slate-50 px-3 py-2"><dt className="font-medium">{label}</dt><dd>{timing === "not_started" ? "시작 안 됨" : formatMillis(timing?.durationMillis ?? "not_started")}</dd></div>; })}</dl><p className="mt-3 text-xs text-slate-600">Token: {current.generationTiming.usageAvailability.tokens} · Cost: {current.generationTiming.usageAvailability.cost} · Worker: {current.generationTiming.worker.status}</p>{current.generationTiming.retryReason && <p className="mt-1 text-xs text-slate-700">Retry reason: {current.generationTiming.retryReason}</p>}{current.generationTiming.terminalError && <p className="mt-1 text-xs text-red-700">Terminal error: {current.generationTiming.terminalError}</p>}</section>}

        {canPrepareAnotherFidelity && <section className="mt-4 rounded-xl border border-violet-200 bg-violet-50/60 p-4"><h3 className="font-semibold text-slate-900">Fidelity 파일 만들기</h3><p className="mt-1 text-sm text-slate-700">화면 보존형 출력만 선택할 수 있습니다. 개체 편집은 지원하지 않습니다.</p>{releaseReviewIncomplete && <p className="mt-2 text-sm font-medium text-slate-700">development 생성은 최종 품질 완료가 아닙니다. Fidelity export가 별도의 release 검토를 시작합니다.</p>}<div className="mt-3 flex flex-wrap gap-2">{!hasPptxArtifact && <button type="button" onClick={() => void requestExport("pptx")} className="rounded-xl bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-800">Fidelity PPTX 준비</button>}{!hasPdfArtifact && <button type="button" onClick={() => void requestExport("pdf")} className="rounded-lg border border-violet-200 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-violet-100">Fidelity PDF 준비</button>}</div></section>}

        {current.artifacts && current.artifacts.length > 0 && <section className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-4" aria-label="다운로드 가능한 Fidelity 파일"><h3 className="font-semibold text-emerald-950">{releaseReviewIncomplete ? "다운로드 가능 · 릴리스 검토 미완료" : "다운로드 준비됨"}</h3><p className="mt-1 text-sm text-emerald-900">모든 파일은 1280×720 화면 보존형 raster이며 개체 편집·접근 가능한 텍스트를 지원하지 않습니다.</p><ul className="mt-3 space-y-2">{current.artifacts.map((artifact) => <li key={artifact.artifactId} className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-white px-3 py-2"><div><p className="text-sm font-semibold text-slate-900">{artifact.kind === "presentation-pdf" ? "Fidelity PDF" : "Fidelity PPTX"}</p><p className="text-xs text-slate-500">{formatBytes(artifact.sizeBytes) ?? "파일 크기 확인 중"}</p></div><a href={artifact.download} download onClick={() => setRequestedDownloadId(artifact.artifactId)} className="rounded-lg bg-emerald-700 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-800">다운로드</a></li>)}</ul>{requestedDownloadId && <p className="mt-3 text-xs text-emerald-900" role="status">다운로드를 요청했습니다. 브라우저의 다운로드 상태를 확인하세요.</p>}{current.downloadStatus && <p className="mt-2 text-xs text-emerald-900">다운로드 상태: {readableStatus(current.downloadStatus)}</p>}</section>}
        {!requestError && current.failure && !isFailed && !isCancelled && <p className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950" role="alert">{requestFailure(current.failure)}{current.phase === "monitoring" ? " 작업 상태를 다시 확인하고 있습니다." : ""}</p>}
        {!requestError && isFailed && !hasOperation && <p className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-900" role="alert">작업 상태를 확인하지 못했습니다. 페이지를 새로 열어 다시 시도하세요.</p>}
        {requestError && <p className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-900" role="alert">요청을 완료하지 못했습니다: {requestError}</p>}
      </section>
    </main>
  );
}
