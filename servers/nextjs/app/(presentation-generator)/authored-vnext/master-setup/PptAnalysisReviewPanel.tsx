"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";

export type PptAnalysisObjective = "faithful" | "cleanup" | "rebrand";
export type PptAnalysisStatus = "idle" | "analyzing" | "completed" | "partial" | "failed";
export type RepeatedTextDecision = "pending" | "fixed" | "input" | "ai-zone" | "remove";

export type PptAnalysisLayoutCandidate = {
  id: string;
  /** Canonical master layout selector, distinct from the analysis candidate ID. */
  selector?: string;
  name: string;
  kind?: string;
  confidence: number;
  slideNumbers: number[];
  representativeSlide?: number;
  evidence?: Array<{ slideNumber: number; title?: string; thumbnailUrl?: string }>;
  differences?: string[];
  elementRoles?: string[];
  excluded?: boolean;
};

export type PptAnalysisRepeatedText = {
  id: string;
  text: string;
  label?: string;
  confidence: number;
  slideNumbers: number[];
  decision?: RepeatedTextDecision;
  aiBinding?: PptAnalysisAiBinding;
};

export type PptAnalysisAiBinding = {
  instruction: string;
  exampleText: string;
  emptyBehavior: "hidden" | "blank" | "fallback";
  fallbackText?: string;
  maxChars?: number;
};

export type PptAnalysisValidationFinding = {
  id: string;
  severity: "warning" | "error" | "info";
  code?: string;
  message: string;
  slideNumbers?: number[];
};

export type PptAnalysisSummary = {
  slideCount: number;
  designFamilies?: string[];
  fonts?: string[];
  colors?: string[];
  repeatedElements?: string[];
  aiZoneCount?: number;
  lowConfidenceCount?: number;
};

/**
 * Presentation-only state for reviewing deterministic PPTX analysis. The
 * parent owns all persistence and converts callbacks to analysis PATCHes or
 * semantic master-contract transactions; no canvas JSON is stored here.
 */
export interface PptAnalysisReviewProps {
  analysisId?: string;
  sourceDigest?: string;
  status: PptAnalysisStatus;
  objective: PptAnalysisObjective;
  summary?: PptAnalysisSummary;
  layouts: PptAnalysisLayoutCandidate[];
  repeatedText?: PptAnalysisRepeatedText[];
  validationFindings?: PptAnalysisValidationFinding[];
  overlay?: boolean;
  /** Rendered original-slide layer; it is non-canonical comparison UI only. */
  sourceOverlay?: ReactNode;
  isReanalyzing?: boolean;
  isSaving?: boolean;
  failureMessage?: string;
  onObjectiveChange?: (objective: PptAnalysisObjective) => void;
  onSelectLayout?: (layoutId: string) => void;
  onLayoutAction?: (
    layoutIds: string[],
    action: "merge" | "split" | "exclude" | "include",
    slideNumbers?: number[],
  ) => void;
  onOverlayChange?: (visible: boolean) => void;
  onRepeatedTextDecision?: (candidateId: string, decision: RepeatedTextDecision) => void;
  onAiBindingChange?: (candidateId: string, binding: PptAnalysisAiBinding) => void;
  onReanalyze?: () => void;
  onRunValidation?: () => void;
  onSave?: () => void;
}

const confidenceTone = (confidence: number) =>
  confidence < 0.55
    ? "bg-amber-100 text-amber-950"
    : confidence < 0.8
      ? "bg-blue-100 text-blue-900"
      : "bg-emerald-100 text-emerald-900";

const findingTone = (severity: PptAnalysisValidationFinding["severity"]) =>
  severity === "error"
    ? "border-red-200 bg-red-50 text-red-900"
    : severity === "warning"
      ? "border-amber-200 bg-amber-50 text-amber-950"
      : "border-blue-200 bg-blue-50 text-blue-950";

const analysisEvidenceMessageKey = (message: string) => {
  const normalized = message.toLowerCase();
  if (normalized.startsWith("featuresidecarreextracted:") || (normalized.includes("sidecar") && (normalized.includes("reextract") || normalized.includes("re-extract") || normalized.includes("missing") || normalized.includes("mismatch")))) return "sidecarReextracted";
  if (normalized.startsWith("forcedmergelowconfidence:") || (normalized.includes("forced") && normalized.includes("merge"))) return "forcedMergeLowConfidence";
  if (normalized.includes("slotevidencepending")) return "slotEvidencePending";
  if (normalized.includes("analysis_update_failed") || normalized.includes("analysis_validation_failed") || normalized.includes("recalculation")) return "recalculationFailed";
  return null;
};

export function PptAnalysisReviewToolbar({ review }: { review: PptAnalysisReviewProps }) {
  const t = useTranslations("authoredVnext.master.analysis");
  if (review.status === "idle") return null;

  return (
    <div className="ml-1 flex flex-wrap items-center gap-1 border-l border-slate-200 pl-2" data-testid="ppt-analysis-toolbar">
      <span className="text-[11px] font-semibold text-slate-600">{t("sourceReview")}</span>
      <button
        type="button"
        aria-pressed={Boolean(review.overlay)}
        className={`rounded border px-2 py-1 ${review.overlay ? "border-indigo-700 bg-indigo-700 text-white" : "bg-white"}`}
        onClick={() => review.onOverlayChange?.(!review.overlay)}
      >
        {t("overlay")}
      </button>
      <button
        type="button"
        className="rounded border px-2 py-1 disabled:opacity-40"
        disabled={review.isReanalyzing || review.status === "analyzing"}
        onClick={review.onReanalyze}
      >
        {review.isReanalyzing || review.status === "analyzing" ? t("recalculating") : t("reanalyze")}
      </button>
    </div>
  );
}

export function PptAnalysisReviewLeftRail({
  review,
  activeLayoutId,
  onSelectLayout,
}: {
  review: PptAnalysisReviewProps;
  activeLayoutId?: string;
  onSelectLayout: (candidate: PptAnalysisLayoutCandidate) => void;
}) {
  const t = useTranslations("authoredVnext.master.analysis");
  const readableEvidence = (message: string) => {
    const key = analysisEvidenceMessageKey(message);
    return key ? t(key) : message;
  };
  const [mergeSelection, setMergeSelection] = useState<string[]>([]);
  const layoutIdsKey = JSON.stringify(review.layouts.map((layout) => layout.id));
  useEffect(() => {
    const knownLayoutIds = new Set<string>(JSON.parse(layoutIdsKey));
    setMergeSelection((current) => {
      const next = current.filter((layoutId) => knownLayoutIds.has(layoutId));
      return next.length === current.length ? current : next;
    });
  }, [layoutIdsKey]);
  const slideCount = review.summary?.slideCount || 0;
  const dominantLayout =
    slideCount >= 8
      ? review.layouts.find(
          (layout) => layout.slideNumbers.length / slideCount >= 0.8,
        )
      : undefined;
  if (review.status === "idle") return null;

  return (
    <section className="mb-4 border-b border-slate-200 pb-3" data-testid="ppt-analysis-layouts">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">{t("generatedLayouts")}</h3>
        {review.summary ? <span className="text-[10px] text-slate-500">{t("slides", { count: review.summary.slideCount })}</span> : null}
      </div>
      <p className="mt-1 text-[10px] text-slate-500">{t("structuralSimilarityHint")}</p>
      {dominantLayout ? (
        <p className="mt-2 rounded border border-amber-300 bg-amber-50 p-2 text-[11px] text-amber-950" data-testid="ppt-analysis-dominant-cluster-warning">
          {t("dominantClusterWarning", {
            name: dominantLayout.name,
            count: dominantLayout.slideNumbers.length,
            total: slideCount,
          })}
        </p>
      ) : null}
      {review.status === "analyzing" ? (
        <p className="mt-2 text-xs text-slate-500">{t("analyzingDescription")}</p>
      ) : review.status === "failed" ? (
        <p className="mt-2 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-800" role="alert">{review.failureMessage ? readableEvidence(review.failureMessage) : t("failed")}</p>
      ) : (
        <div className="mt-2 space-y-1.5">
          {review.isReanalyzing ? <p className="rounded border border-blue-200 bg-blue-50 p-2 text-[11px] text-blue-950" data-testid="ppt-analysis-recalculating" role="status">{t("recalculatingDescription")}</p> : null}
          {review.failureMessage ? <p className="rounded border border-red-200 bg-red-50 p-2 text-[11px] text-red-900" data-testid="ppt-analysis-recalculation-failed" role="alert">{readableEvidence(review.failureMessage)}</p> : null}
          {review.layouts.map((layout) => {
            const active = activeLayoutId === (layout.selector || layout.id);
            const representativeSlide =
              layout.representativeSlide ?? layout.slideNumbers[0];
            return (
              <article key={layout.id} className={`rounded border p-2 ${active ? "border-indigo-500 bg-indigo-50" : "border-slate-200 bg-white"}`}>
                <button type="button" className="w-full text-left" aria-current={active ? "page" : undefined} onClick={() => onSelectLayout(layout)}>
                  <span className="block truncate text-xs font-semibold">{layout.name}</span>
                  <span className="mt-1 flex items-center justify-between text-[10px] text-slate-500">
                    <span>{t("evidenceSlides", { slides: layout.slideNumbers.join(", ") || "—" })}</span>
                    <span className={`rounded-full px-1.5 py-0.5 font-bold ${confidenceTone(layout.confidence)}`}>{t("structuralSimilarity", { value: Math.round(layout.confidence * 100) })}</span>
                  </span>
                  <span className="mt-1 block text-[10px] text-slate-500">{representativeSlide ? t("representativeSlide", { number: representativeSlide }) : t("noRepresentativeSlide")}</span>
                </button>
                {active ? (
                  <div className="mt-2 flex flex-wrap gap-1">
                    <button type="button" className="rounded border bg-white px-1.5 py-0.5 text-[10px] disabled:opacity-40" disabled={review.isReanalyzing || mergeSelection.length < 2} onClick={() => review.onLayoutAction?.(mergeSelection, "merge")}>{t("merge")}</button>
                    <button type="button" className="rounded border bg-white px-1.5 py-0.5 text-[10px] disabled:opacity-40" disabled={review.isReanalyzing || layout.slideNumbers.length < 2} onClick={() => review.onLayoutAction?.([layout.id], "split", [layout.representativeSlide ?? layout.slideNumbers[0]])}>{t("split")}</button>
                    <button type="button" className="rounded border border-red-200 bg-white px-1.5 py-0.5 text-[10px] text-red-700 disabled:opacity-40" disabled={review.isReanalyzing} onClick={() => review.onLayoutAction?.([layout.id], layout.excluded ? "include" : "exclude")}>{layout.excluded ? t("restore") : t("exclude")}</button>
                  </div>
                ) : null}
                <label className="mt-2 flex items-center gap-1 text-[10px] text-slate-600">
                  <input
                    aria-label={t("selectForMerge", { name: layout.name })}
                    checked={mergeSelection.includes(layout.id)}
                    disabled={review.isReanalyzing}
                    type="checkbox"
                    onChange={(event) => setMergeSelection((current) => event.target.checked ? [...current, layout.id] : current.filter((id) => id !== layout.id))}
                  />
                  {t("selectForMergeLabel")}
                </label>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function AiBindingEditor({ candidate, onChange }: { candidate: PptAnalysisRepeatedText; onChange?: (binding: PptAnalysisAiBinding) => void }) {
  const t = useTranslations("authoredVnext.master.analysis");
  const [draft, setDraft] = useState<PptAnalysisAiBinding>(() => candidate.aiBinding || {
    instruction: t("defaultInstruction", { text: candidate.text }),
    exampleText: candidate.text,
    emptyBehavior: "hidden",
    fallbackText: "",
    maxChars: 80,
  });
  useEffect(() => {
    if (candidate.aiBinding) setDraft(candidate.aiBinding);
  }, [candidate.aiBinding]);
  const update = (next: PptAnalysisAiBinding) => {
    setDraft(next);
    onChange?.(next);
  };
  return (
    <fieldset className="mt-2 space-y-2 rounded border border-violet-200 bg-violet-50 p-2">
      <legend className="px-1 text-[11px] font-semibold text-violet-950">{t("aiZone")}</legend>
      <label className="block text-[11px]">{t("instruction")}<textarea className="mt-1 min-h-14 w-full rounded border border-violet-200 bg-white p-1" value={draft.instruction} onChange={(event) => update({ ...draft, instruction: event.target.value })} /></label>
      <label className="block text-[11px]">{t("example")}<input className="mt-1 w-full rounded border border-violet-200 bg-white p-1" value={draft.exampleText} onChange={(event) => update({ ...draft, exampleText: event.target.value })} /></label>
      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <label>{t("whenEmpty")}<select className="mt-1 w-full rounded border border-violet-200 bg-white p-1" value={draft.emptyBehavior} onChange={(event) => update({ ...draft, emptyBehavior: event.target.value as PptAnalysisAiBinding["emptyBehavior"] })}><option value="hidden">{t("hidden")}</option><option value="blank">{t("blank")}</option><option value="fallback">{t("fallback")}</option></select></label>
        <label>{t("maxChars")}<input className="mt-1 w-full rounded border border-violet-200 bg-white p-1" min="1" type="number" value={draft.maxChars ?? ""} onChange={(event) => update({ ...draft, maxChars: event.target.value ? Math.max(1, Number(event.target.value)) : undefined })} /></label>
      </div>
      {draft.emptyBehavior === "fallback" ? <label className="block text-[11px]">{t("fallbackText")}<input className="mt-1 w-full rounded border border-violet-200 bg-white p-1" value={draft.fallbackText ?? ""} onChange={(event) => update({ ...draft, fallbackText: event.target.value })} /></label> : null}
    </fieldset>
  );
}

export function PptAnalysisReviewInspector({ review, activeLayoutId }: { review: PptAnalysisReviewProps; activeLayoutId?: string }) {
  const t = useTranslations("authoredVnext.master.analysis");
  const readableEvidence = (message: string) => {
    const key = analysisEvidenceMessageKey(message);
    return key ? t(key) : message;
  };
  const active = review.layouts.find(
    (layout) => (layout.selector || layout.id) === activeLayoutId,
  );
  if (review.status === "idle") return null;
  return (
    <section className="mb-4 border-b border-slate-200 pb-3 text-xs" data-testid="ppt-analysis-inspector">
      <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold">{t("evidence")}</h3><span className="flex gap-1"><button type="button" className="rounded border px-1.5 py-0.5 text-[10px] disabled:opacity-40" disabled={review.isSaving} onClick={review.onSave}>{review.isSaving ? t("saving") : t("saveDecisions")}</button><button type="button" className="rounded border px-1.5 py-0.5 text-[10px]" onClick={review.onRunValidation}>{t("reapplyCheck")}</button></span></div>
      {review.status === "partial" || (review.summary?.lowConfidenceCount || 0) > 0 ? <p className="mt-2 rounded border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-950">{t("lowConfidenceWarning")}</p> : null}
      {review.summary ? <div className="mt-2 rounded bg-slate-50 p-2 text-[11px]"><h4 className="font-medium">{t("analysisSummary")}</h4>{review.summary.designFamilies?.length ? <p className="mt-1 text-slate-600">{t("designFamilies", { values: review.summary.designFamilies.join(", ") })}</p> : null}{review.summary.fonts?.length ? <p className="mt-1 text-slate-600">{t("fonts", { values: review.summary.fonts.join(", ") })}</p> : null}{review.summary.colors?.length ? <p className="mt-1 text-slate-600">{t("colors", { values: review.summary.colors.join(", ") })}</p> : null}<p className="mt-1 text-slate-600">{t("repeatedElements", { count: review.summary.repeatedElements?.length || review.repeatedText?.length || 0 })} · {t("aiZoneCandidates", { count: review.summary.aiZoneCount || 0 })}</p></div> : null}
      {active ? <div className="mt-2 space-y-2"><div className="rounded bg-slate-50 p-2"><div className="font-medium">{active.name}</div><div className="mt-1 text-[11px] text-slate-600">{t("structuralSimilarity", { value: Math.round(active.confidence * 100) })}</div>{active.elementRoles?.length ? <p className="mt-1 text-[11px] text-slate-600">{t("roles", { roles: active.elementRoles.join(", ") })}</p> : null}</div>{active.evidence?.length ? <div><h4 className="font-medium">{t("sourceSlides")}</h4><div className="mt-1 flex flex-wrap gap-1">{active.evidence.map((item) => <span key={item.slideNumber} className="rounded bg-slate-100 px-1.5 py-1 text-[10px]">{t("slide", { number: item.slideNumber })}{item.title ? ` · ${item.title}` : ""}</span>)}</div></div> : null}{active.differences?.length ? <div><h4 className="font-medium">{t("differences")}</h4><ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] text-slate-600">{active.differences.map((difference) => <li key={difference}>{readableEvidence(difference)}</li>)}</ul></div> : null}</div> : <p className="mt-2 text-[11px] text-slate-500">{t("selectLayout")}</p>}
      {review.repeatedText?.length ? <div className="mt-3"><h4 className="font-semibold">{t("repeatedText")}</h4><p className="mt-1 text-[11px] text-slate-500">{t("repeatedTextHelp")}</p><div className="mt-2 space-y-2">{review.repeatedText.map((candidate) => <article key={candidate.id} className="rounded border border-slate-200 p-2"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><p className="truncate font-medium">{candidate.label || t("candidate")}</p><p className="mt-0.5 truncate text-[11px] text-slate-700">{candidate.text}</p><p className="text-[10px] text-slate-500">{t("evidenceSlides", { slides: candidate.slideNumbers.join(", ") })}</p></div><span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-bold ${confidenceTone(candidate.confidence)}`}>{t("confidence", { value: Math.round(candidate.confidence * 100) })}</span></div><label className="mt-2 block text-[11px]">{t("decision")}<select className="mt-1 w-full rounded border p-1" value={candidate.decision || "pending"} onChange={(event) => review.onRepeatedTextDecision?.(candidate.id, event.target.value as RepeatedTextDecision)}><option value="pending" disabled>{t("pending")}</option><option value="fixed">{t("fixed")}</option><option value="input">{t("input")}</option><option value="ai-zone">{t("aiZone")}</option><option value="remove">{t("remove")}</option></select></label>{candidate.decision === "ai-zone" ? <AiBindingEditor candidate={candidate} onChange={(binding) => review.onAiBindingChange?.(candidate.id, binding)} /> : null}</article>)}</div></div> : null}
      {review.validationFindings?.length ? <div className="mt-3"><h4 className="font-semibold">{t("reapplyWarnings")}</h4><div className="mt-1 space-y-1">{review.validationFindings.map((finding) => <p className={`rounded border p-2 text-[11px] ${findingTone(finding.severity)}`} key={finding.id}>{finding.code ? `${finding.code}: ` : ""}{readableEvidence(finding.message)}{finding.slideNumbers?.length ? ` · ${t("slides", { count: finding.slideNumbers.length })}` : ""}</p>)}</div></div> : null}
    </section>
  );
}

export function PptAnalysisObjectivePicker({ review }: { review: PptAnalysisReviewProps }) {
  const t = useTranslations("authoredVnext.master.analysis");
  return <fieldset className="grid gap-2 rounded-xl border border-slate-200 bg-white p-3" data-testid="ppt-analysis-objective"><legend className="px-1 text-sm font-semibold">{t("objective")}</legend><p className="text-xs text-slate-500">{t("objectiveHelp")}</p><div className="grid gap-2 sm:grid-cols-3">{(["faithful", "cleanup", "rebrand"] as const).map((objective) => <label key={objective} className={`cursor-pointer rounded border p-2 text-xs ${review.objective === objective ? "border-indigo-500 bg-indigo-50" : "border-slate-200"}`}><input className="mr-1.5" checked={review.objective === objective} name="ppt-analysis-objective" type="radio" value={objective} onChange={() => review.onObjectiveChange?.(objective)} />{t(`objectives.${objective}`)}</label>)}</div></fieldset>;
}
