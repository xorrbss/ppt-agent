"use client";

import { FormEvent, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

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
  svgDesignReferenceSnapshotId: "",
  svgComposition: false,
  canvas: "16:9",
  canvasWidth: "320",
  canvasHeight: "180",
  designPackId: "",
  designPackVersion: "",
  designPackDigest: "",
};

const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/;

const GENERATION_MODE_LABEL_KEYS = {
  auto: "automaticSelection",
  "registered-template": "useRegisteredTemplateAsIs",
  "brand-style": "applyBrandStyle",
  "free-design": "freeDesign",
  "fidelity-first": "prioritizeFidelity",
  "editable-first": "prioritizeEditability",
} as const satisfies Record<PublicV2GenerationMode, string>;

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

const TIMING_STAGE_LABELS = {
  outline: "timingOutline",
  provider: "timingProvider",
  authoring: "timingAuthoring",
  repair: "timingRepair",
  chromiumReview: "timingChromiumReview",
  publication: "timingPublication",
} as const;

function statusTone(status?: string): string {
  if (status === "succeeded" || status === "ready") return "border-emerald-200 bg-emerald-50 text-emerald-900";
  if (status === "failed" || status === "blocked") return "border-red-200 bg-red-50 text-red-900";
  if (status === "cancelled") return "border-slate-300 bg-slate-100 text-slate-800";
  return "border-blue-200 bg-blue-50 text-blue-900";
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

export default function PublicV2OperationsClient({ storageScope }: { storageScope: string }) {
  const t = useTranslations("publicV2");
  const formatMillis = (value: number | "not_started") => value === "not_started" ? t("notStarted") : `${value.toLocaleString()} ms`;
  const formatCounter = (value: number | "not_started") => value === "not_started" ? t("notStarted") : value.toLocaleString();
  const readableStatus = (status?: string, releaseReadiness?: string): string => {
    if (status === "succeeded" && releaseReadiness !== "ready") return t("releaseReviewIncomplete");
    switch (status) {
      case "queued": return t("statusQueued");
      case "running": return t("statusRunning");
      case "succeeded": return t("statusSucceeded");
      case "failed": return t("statusFailed");
      case "cancelled": return t("statusCancelled");
      case "ready": return t("statusReady");
      case "blocked": return t("statusBlocked");
      case "not_assessed": return t("statusNotAssessed");
      default: return status ?? t("statusNotStarted");
    }
  };
  const requestFailure = (error: unknown): string => {
    const reasonCode = error && typeof error === "object" && "reasonCode" in error
      ? (error as { reasonCode?: unknown }).reasonCode
      : error;
    switch (reasonCode) {
      case "generation_content_required": return t("failureContentRequired");
      case "public_v2_slide_count_must_be_between_1_and_100": return t("failureSlideCountRange");
      case "public_v2_registered_template_pin_required": return t("failureTemplatePinRequired");
      case "public_v2_source_revision_unavailable": return t("failureSourceRevisionUnavailable");
      case "public_v2_network_error": return t("failureNetworkError");
      default: return t("failureUnknown");
    }
  };
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
    const svgComposition = form.svgComposition && ["auto", "free-design"].includes(form.generationMode);
    const svgDesignReferenceSnapshotId = form.svgDesignReferenceSnapshotId.trim();
    if (svgComposition && svgDesignReferenceSnapshotId && (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(svgDesignReferenceSnapshotId) || form.designPackId.trim())) {
      setRequestError("유효한 디자인 참고 UUID를 입력하고 등록 디자인 참조와 하나만 선택해 주세요."); return;
    }
    if (form.generationMode === "registered-template" || (svgComposition && form.designPackId.trim())) {
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
    const widthMm = Number(form.canvasWidth); const heightMm = Number(form.canvasHeight);
    if (svgComposition && form.canvas === "custom" && !([widthMm, heightMm].every((value) => Number.isFinite(value) && value >= 50 && value <= 1000 && Math.abs(value * 100 - Math.round(value * 100)) < 1e-7) && widthMm / heightMm >= 0.25 && widthMm / heightMm <= 4)) {
      setRequestError("각 변은 50–1000 mm, 소수 둘째 자리까지 입력하고 비율은 1:4–4:1로 설정해 주세요."); return;
    }
    setRequestError(null);
    setRequestedDownloadId(null);
    try {
      await submitGenerationRequest({
        content,
        ...(form.instructions.trim() ? { instructions: form.instructions.trim() } : {}),
        ...(requestedSlideCount === undefined ? {} : { slideCount: requestedSlideCount }),
        generationMode: form.generationMode,
        ...(svgComposition && svgDesignReferenceSnapshotId ? { svgDesignReferenceSnapshotId } : {}),
        ...(designPackPin ? svgComposition ? { svgDesignReferencePin: designPackPin } : { designPackPin } : {}),
        ...(svgComposition ? { strategy: "svg" as const, ...(form.canvas === "custom" ? { svgCanvas: { widthMm, heightMm } } : form.canvas.startsWith("a4-") ? { pageSize: "A4" as const, orientation: form.canvas === "a4-portrait" ? "portrait" as const : "landscape" as const } : { aspectRatio: form.canvas as "16:9" | "9:16" | "9:13" | "4:3" | "1:1" | "21:9" }) } : {}),
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
        <p className="inline-flex rounded-full bg-violet-50 px-3 py-1 text-[11px] font-semibold tracking-[0.12em] text-violet-600">{t("publicV2")}</p>
        <h1 className="mt-4 text-2xl font-semibold leading-tight tracking-[-0.035em] sm:text-3xl">{t("createPresentationsAndDownloadFidelityFiles")}</h1>
        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
          {t("generationCreatesADevelopmentDraftWhileFile")}
        </p>
      </header>

      <div className="mt-6 grid min-w-0 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_280px]">
        <section className="min-w-0 rounded-3xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_30px_-18px_rgba(15,23,42,0.18)] sm:p-7" aria-labelledby="generation-title">
            <h2 id="generation-title" className="text-lg font-bold">{t("newPresentation")}</h2>
            <form className="mt-5 space-y-5" onSubmit={submitGeneration}>
              {current.pendingAdmission && (
                <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950" role="status">
                  <p className="font-semibold">{t("theAdmissionResultNeedsToBeRecovered")}</p>
                  <p className="mt-1 leading-5">{pendingExport ? t("theConnectionEndedBeforeThePreviousFile") : t("theConnectionEndedBeforeThePreviousGeneration")}</p>
                  {pendingExport && <button type="button" disabled={current.phase === "submitting"} onClick={() => void recoverExportAdmission()} className="mt-2 mr-2 rounded-lg bg-amber-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-800 disabled:cursor-not-allowed disabled:opacity-60">{t("recoverFilePreparationAdmission")}</button>}
                  <button type="button" disabled={current.phase === "submitting"} onClick={abandonPendingAdmission} className="mt-2 rounded-lg border border-amber-500 px-3 py-1.5 text-xs font-semibold hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60">{t("abandonThisAdmissionAndStartANew")}</button>
                </div>
              )}
            <label className="block min-w-0 text-sm font-medium text-slate-700" htmlFor="public-v2-content">
              {t("content")}
              <textarea id="public-v2-content" required value={form.content} onChange={(event) => setForm((value) => ({ ...value, content: event.target.value }))} className="mt-2 min-h-44 w-full resize-y rounded-2xl border border-slate-200 bg-slate-50/50 px-4 py-3 text-base leading-7 outline-none transition-colors placeholder:text-slate-400 focus:border-violet-300 focus:bg-white focus:ring-4 focus:ring-violet-100/60" placeholder={t("enterThePurposeAndKeyContentOf")} />
            </label>
            <label className="block min-w-0 text-sm font-medium text-slate-700" htmlFor="public-v2-instructions">
              {t("additionalInstructions")} <span className="font-normal text-slate-500">{t("optional")}</span>
              <input id="public-v2-instructions" value={form.instructions} onChange={(event) => setForm((value) => ({ ...value, instructions: event.target.value }))} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-slate-400 focus:border-violet-300 focus:ring-4 focus:ring-violet-100/60" placeholder={t("audienceToneAndPointsToEmphasize")} />
            </label>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block min-w-0 text-sm font-medium text-slate-700" htmlFor="public-v2-generation-mode">
                {t("designApproach")} <span className="font-normal text-slate-500">{t("chooseAgainForEachDocument")}</span>
                <select id="public-v2-generation-mode" value={form.generationMode} onChange={(event) => setForm((value) => ({ ...value, generationMode: event.target.value as PublicV2GenerationMode }))} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-slate-400 focus:border-violet-300 focus:ring-4 focus:ring-violet-100/60">
                  <option value="auto">{t("automaticSelection")}</option>
                  <option value="registered-template">{t("useRegisteredTemplateAsIs")}</option>
                  <option value="brand-style">{t("applyBrandStyle")}</option>
                  <option value="free-design">{t("freeDesign")}</option>
                  <option value="fidelity-first">{t("prioritizeFidelity")}</option>
                  <option value="editable-first" disabled>{t("prioritizeEditabilityCurrentlyUnsupported")}</option>
                </select>
                <span className="mt-1 block text-xs font-normal text-slate-500">{t("editabilityFirstIsUnavailableUnderThePublic")}</span>
              </label>
              <label className="block min-w-0 text-sm font-medium text-slate-700" htmlFor="public-v2-slide-count">
                {t("numberOfSlides")} <span className="font-normal text-slate-500">{t("optional")}</span>
                <input id="public-v2-slide-count" min={1} max={100} inputMode="numeric" type="number" value={form.slideCount} onChange={(event) => setForm((value) => ({ ...value, slideCount: event.target.value }))} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition-colors placeholder:text-slate-400 focus:border-violet-300 focus:ring-4 focus:ring-violet-100/60" />
              </label>
            </div>
            {["auto", "free-design"].includes(form.generationMode) && <div className="space-y-3 rounded-xl border border-slate-200 p-4">
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.svgComposition} onChange={(event) => setForm((value) => ({ ...value, svgComposition: event.target.checked }))} />SVG 자유 구성</label>
              {form.svgComposition && <><label className="block text-sm">캔버스<select aria-label="SVG 캔버스" value={form.canvas} onChange={(event) => setForm((value) => ({ ...value, canvas: event.target.value }))} className="ml-3 rounded border p-2">{["16:9", "9:16", "9:13", "4:3", "1:1", "21:9", "a4-landscape", "a4-portrait", "custom"].map((canvas) => <option key={canvas} value={canvas}>{canvas === "a4-landscape" ? "A4 가로" : canvas === "a4-portrait" ? "A4 세로" : canvas === "custom" ? "사용자 지정 (mm)" : canvas}</option>)}</select></label>{form.canvas === "custom" && <div className="flex gap-2"><input aria-label="SVG 너비 (mm)" type="number" min="50" max="1000" step="0.01" value={form.canvasWidth} onChange={(event) => setForm((value) => ({ ...value, canvasWidth: event.target.value }))} className="w-1/2 rounded border p-2" /><input aria-label="SVG 높이 (mm)" type="number" min="50" max="1000" step="0.01" value={form.canvasHeight} onChange={(event) => setForm((value) => ({ ...value, canvasHeight: event.target.value }))} className="w-1/2 rounded border p-2" /></div>}<p className="text-xs text-slate-600">선택한 디자인은 색상·서체·분위기 참고로 사용하고 배치는 모델이 구성합니다.</p></>}
            </div>}
            {form.svgComposition && ["auto", "free-design"].includes(form.generationMode) ? <label className="block text-sm text-slate-700">디자인 참고 파일 UUID (선택)<input aria-label="디자인 참고 파일 UUID" value={form.svgDesignReferenceSnapshotId} onChange={(event) => setForm((value) => ({ ...value, svgDesignReferenceSnapshotId: event.target.value }))} className="mt-2 w-full rounded border p-2" placeholder="등록한 디자인 참고 파일의 UUID" /></label> : null}
            {(form.generationMode === "registered-template" || (form.svgComposition && ["auto", "free-design"].includes(form.generationMode))) && <fieldset className="rounded-xl border border-slate-200 bg-slate-50 p-4"><legend className="px-1 text-sm font-semibold text-slate-800">{form.generationMode === "registered-template" ? t("registeredTemplatePin") : "디자인 참고 (선택)"}</legend><div className="grid min-w-0 gap-3 sm:grid-cols-3"><label className="text-xs font-medium text-slate-700" htmlFor="public-v2-design-pack-id">{t("designPackId")}<input id="public-v2-design-pack-id" required={form.generationMode === "registered-template"} value={form.designPackId} onChange={(event) => setForm((value) => ({ ...value, designPackId: event.target.value }))} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" /></label><label className="text-xs font-medium text-slate-700" htmlFor="public-v2-design-pack-version">{t("version")}<input id="public-v2-design-pack-version" required={form.generationMode === "registered-template" || Boolean(form.designPackId.trim())} min={1} inputMode="numeric" type="number" value={form.designPackVersion} onChange={(event) => setForm((value) => ({ ...value, designPackVersion: event.target.value }))} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" /></label><label className="text-xs font-medium text-slate-700" htmlFor="public-v2-design-pack-digest">{t("digest")}<input id="public-v2-design-pack-digest" required={form.generationMode === "registered-template" || Boolean(form.designPackId.trim())} pattern="sha256:[0-9a-f]{64}" value={form.designPackDigest} onChange={(event) => setForm((value) => ({ ...value, designPackDigest: event.target.value }))} placeholder={t("sha256")} className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-slate-200 bg-white px-3 py-2 font-mono text-sm" /></label></div></fieldset>}
            <button type="submit" disabled={pendingExport || current.phase === "submitting" || current.phase === "monitoring"} className="inline-flex min-h-11 w-full items-center justify-center rounded-xl bg-slate-900 px-5 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-violet-200 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto">
              {pendingExport ? t("fileAdmissionRecoveryRequired") : current.pendingAdmission ? t("retryAdmissionRecovery") : t("startGeneration")}
            </button>
          </form>
        </section>

        <aside className="min-w-0 rounded-2xl border border-slate-200/80 bg-slate-100/70 p-5" aria-label={t("fidelitySupportScope")}>
          <h2 className="font-semibold text-slate-800">{t("fidelityOutputOnly")}</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
             {t("pptxAndPdfPreserveThe1280720")}
          </p>
          <p className="mt-3 text-xs leading-5 text-slate-500">
             {t("finalCompletionRequiresReleaseReceiptsToPass")}
          </p>
        </aside>
      </div>

      <section className="mt-6 min-w-0 rounded-3xl border border-slate-200/80 bg-white p-5 shadow-[0_8px_30px_-18px_rgba(15,23,42,0.18)] sm:p-7" aria-live="polite" aria-labelledby="operation-title">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="operation-title" className="text-lg font-bold">{t("operationStatus")}</h2>
            {current.operationId && <p className="mt-1 break-all font-mono text-xs text-slate-500">{t("operationId")} {current.operationId}</p>}
          </div>
          <span className={`max-w-full rounded-full border px-3 py-1.5 text-xs font-semibold ${statusTone(current.status ?? current.phase)}`}>{readableStatus(current.status ?? current.phase, current.quality?.releaseReadiness)}</span>
        </div>

        {!hasOperation && <p className="mt-5 rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-5 py-8 text-sm leading-6 text-slate-500">{t("afterGenerationStartsCheckResumableOperationAnd")}</p>}
        {hasOperation && (
          <div className="mt-5 space-y-5">
            <div className="rounded-xl bg-slate-50 p-4 text-sm text-slate-700">
              <div className="flex flex-wrap justify-between gap-2"><span>{t("currentPhase")} {current.operationPhase ?? t("checkingStatus")}</span><span>{typeof current.progressPercent === "number" ? `${current.progressPercent}%` : t("checkingProgress")}</span></div>
              {typeof current.progressPercent === "number" && <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-violet-500 transition-all motion-reduce:transition-none" style={{ width: `${current.progressPercent}%` }} /></div>}
            </div>
            {!terminal && current.canCancel && <button type="button" onClick={() => void requestCancel()} className="rounded-lg border border-red-300 px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50">{t("cancelThisOperation")}</button>}
            {isCancelled && <div className="rounded-xl border border-slate-300 bg-slate-50 p-4 text-sm text-slate-800" role="status"><p className="font-semibold">{t("theOperationWasCanceled")}</p><p className="mt-1">{current.failure?.reasonCode ? t("theCancellationRequestWasProcessed") : t("theCancellationRequestWasProcessedSeparatelyFrom")}</p></div>}
            {isFailed && <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900" role="alert"><p className="font-semibold">{t("theOperationFailed")}</p><p className="mt-1">{t("failureReason")} {requestFailure(current.failure)}</p><p className="mt-1 text-xs">{current.failure?.retryable ? t("thisErrorCanBeRetried") : t("checkTheInputAndQualityStatusBefore")}</p></div>}
            {current.generationRouteReceipt && <section className="rounded-xl border border-slate-200 bg-white p-4" aria-label={t("serverExecutionRouteReceipt")}><h3 className="font-semibold text-slate-900">{t("selectedGenerationMode")}</h3><dl className="mt-2 grid gap-2 text-sm sm:grid-cols-2"><div><dt className="text-slate-500">{t("requestedMode")}</dt><dd>{t(GENERATION_MODE_LABEL_KEYS[current.generationRouteReceipt.requestedMode])}</dd></div><div><dt className="text-slate-500">{t("serverSelection")}</dt><dd>{current.generationRouteReceipt.selectedRoute === "source-native" ? t("useRegisteredSourceStructure") : t("freeDesignFallbackRoute")}</dd></div><div><dt className="text-slate-500">{t("outputCapabilities")}</dt><dd>{current.generationRouteReceipt.outputCapability === "editable" ? t("editable") : t("fidelity")}</dd></div>{current.generationRouteReceipt.designPackPin && <div><dt className="text-slate-500">{t("registeredTemplate")}</dt><dd>{current.generationRouteReceipt.designPackPin.designPackId} {t("v")}{current.generationRouteReceipt.designPackPin.version}</dd></div>}</dl>{current.generationRouteReceipt.fallbackReason && <p className="mt-2 text-xs text-amber-800">{t("fallbackRouteReason")} {current.generationRouteReceipt.fallbackReason}</p>}</section>}
          </div>
        )}

        {current.quality && <section className={`mt-4 rounded-xl border p-4 ${statusTone(current.quality.releaseReadiness)}`} aria-label={t("qualityReadiness")}><h3 className="font-semibold">{t("qualityReadiness2")} {readableStatus(current.quality.releaseReadiness)}</h3>{current.quality.releaseReadiness !== "ready" && <p className="mt-1 text-sm">{current.quality.releaseReadiness === "not_assessed" ? t("visualPerformanceAndNativeCoverageChecksHave") : t("releaseReadinessIsBlockedBecauseRequiredQuality")}</p>}{checks.length > 0 && <ul className="mt-3 space-y-2 text-sm">{checks.map((check) => <li key={check.label} className="rounded-lg bg-white/60 px-3 py-2"><span className="font-medium">{check.label}</span>: {readableStatus(check.status)}{check.required ? t("required") : ""}{check.reasonCode ? ` · ${check.reasonCode}` : ""}</li>)}</ul>}</section>}

        <section className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4" aria-label={t("independentReadiness")}>
          <h3 className="font-semibold text-slate-900">{t("independentReadiness")}</h3>
          <p className="mt-1 text-xs text-slate-600">{t("scope")} {current.readiness.scope}{t("preparingAFidelityFileDoesNotImply")}</p>
          <dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
            <div className="rounded-lg bg-white px-3 py-2"><dt className="font-medium">{t("fidelityArtifact")}</dt><dd>{current.readiness.fidelityArtifactReady ? t("ready") : t("notReady")}</dd></div>
            <div className="rounded-lg bg-white px-3 py-2"><dt className="font-medium">{t("nativeEditability")}</dt><dd>{current.readiness.nativeEditabilityReady ? t("ready") : t("notReady")}</dd></div>
            <div className="rounded-lg bg-white px-3 py-2"><dt className="font-medium">{t("promotion")}</dt><dd>{current.readiness.promotionReady ? t("ready") : t("notPromoted")}</dd></div>
            <div className="rounded-lg bg-white px-3 py-2"><dt className="font-medium">{t("deploymentAuthorization")}</dt><dd>{current.readiness.deploymentAuthorized ? t("authorized") : t("notAuthorized")}</dd></div>
          </dl>
        </section>

        {current.generationTiming && <section className="mt-4 rounded-xl border border-slate-200 bg-white p-4" aria-label={t("generationPhaseTiming")}><h3 className="font-semibold text-slate-900">{t("generationPhaseTiming")}</h3><p className="mt-1 text-xs text-slate-600">{t("attempt")} {current.generationTiming.attemptNumber} {t("queueWait")} {formatMillis(current.generationTiming.queueWaitMillis)} {t("wall")} {formatMillis(current.generationTiming.wallMillis)} {t("logicalCalls")} {formatCounter(current.generationTiming.logicalCalls)}</p><dl className="mt-3 grid gap-2 text-sm sm:grid-cols-2">{Object.entries(TIMING_STAGE_LABELS).map(([stage, label]) => { const timing = current.generationTiming?.stages[stage as keyof typeof TIMING_STAGE_LABELS]; return <div key={stage} className="rounded-lg bg-slate-50 px-3 py-2"><dt className="font-medium">{t(label)}</dt><dd>{timing === "not_started" ? t("notStarted") : formatMillis(timing?.durationMillis ?? "not_started")}</dd></div>; })}</dl><p className="mt-3 text-xs text-slate-600">{t("tokens")} {current.generationTiming.usageAvailability.tokens} {t("cost")} {current.generationTiming.usageAvailability.cost} {t("worker")} {current.generationTiming.worker.status}</p>{current.generationTiming.retryReason && <p className="mt-1 text-xs text-slate-700">{t("retryReason")} {current.generationTiming.retryReason}</p>}{current.generationTiming.terminalError && <p className="mt-1 text-xs text-red-700">{t("terminalError")} {current.generationTiming.terminalError}</p>}</section>}

        {canPrepareAnotherFidelity && <section className="mt-4 rounded-xl border border-violet-200 bg-violet-50/60 p-4"><h3 className="font-semibold text-slate-900">{t("createFidelityFiles")}</h3><p className="mt-1 text-sm text-slate-700">{t("onlyScreenPreservingOutputIsAvailableObject")}</p>{releaseReviewIncomplete && <p className="mt-2 text-sm font-medium text-slate-700">{t("developmentGenerationIsNotFinalQualityCompletion")}</p>}<div className="mt-3 flex flex-wrap gap-2">{!hasPptxArtifact && <button type="button" onClick={() => void requestExport("pptx")} className="rounded-xl bg-slate-900 px-3 py-2 text-sm font-semibold text-white hover:bg-slate-800">{t("prepareFidelityPptx")}</button>}{!hasPdfArtifact && <button type="button" onClick={() => void requestExport("pdf")} className="rounded-lg border border-violet-200 px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-violet-100">{t("prepareFidelityPdf")}</button>}</div></section>}

        {current.artifacts && current.artifacts.length > 0 && <section className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-4" aria-label={t("downloadableFidelityFiles")}><h3 className="font-semibold text-emerald-950">{releaseReviewIncomplete ? t("availableToDownloadReleaseReviewIncomplete") : t("readyToDownload")}</h3><p className="mt-1 text-sm text-emerald-900">{"파일은 문서에 저장된 캔버스 크기로 생성됩니다."}</p><ul className="mt-3 space-y-2">{current.artifacts.map((artifact) => <li key={artifact.artifactId} className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-white px-3 py-2"><div><p className="text-sm font-semibold text-slate-900">{artifact.kind === "presentation-pdf" ? t("fidelityPdf") : t("fidelityPptx")}</p><p className="text-xs text-slate-500">{formatBytes(artifact.sizeBytes) ?? t("checkingFileSize")}</p></div><a href={artifact.download} download onClick={() => setRequestedDownloadId(artifact.artifactId)} className="rounded-lg bg-emerald-700 px-3 py-2 text-sm font-semibold text-white hover:bg-emerald-800">{t("download")}</a></li>)}</ul>{requestedDownloadId && <p className="mt-3 text-xs text-emerald-900" role="status">{t("downloadRequestedCheckYourBrowserSDownload")}</p>}{current.downloadStatus && <p className="mt-2 text-xs text-emerald-900">{t("downloadStatus")} {readableStatus(current.downloadStatus)}</p>}</section>}
        {!requestError && current.failure && !isFailed && !isCancelled && <p className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950" role="alert">{requestFailure(current.failure)}{current.phase === "monitoring" ? t("checkingOperationStatusAgain") : ""}</p>}
        {!requestError && isFailed && !hasOperation && <p className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-900" role="alert">{t("couldNotCheckOperationStatusReopenThe")}</p>}
        {requestError && <p className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-900" role="alert">{t("couldNotCompleteTheRequest")} {requestError}</p>}
      </section>
    </main>
  );
}
