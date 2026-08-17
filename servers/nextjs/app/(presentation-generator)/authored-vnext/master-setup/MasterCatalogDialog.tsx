"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Ban } from "lucide-react";
import { useTranslations } from "next-intl";

import {
  AuthoredVnextMasterClient,
  MasterApiError,
} from "@/lib/authored-vnext/master/client";
import type {
  AiMasterAnalysis,
  AiMasterCandidateDecision,
  AiMasterLayoutAction,
  AiMasterPurpose,
  DeckMasterContractV1,
  MasterCatalogSummary,
  MasterRevisionEnvelope,
} from "@/lib/authored-vnext/master/contracts";

import MasterCatalogCard from "./MasterCatalogCard";
import MasterSetupGate from "./MasterSetupGate";
import {
  PptAnalysisObjectivePicker,
  type PptAnalysisAiBinding,
  type PptAnalysisObjective,
  type PptAnalysisReviewProps,
  type RepeatedTextDecision,
} from "./PptAnalysisReviewPanel";

type CatalogView = "catalog" | "objective" | "designer";
type DesignerMode = "new" | "edit";
export type MasterCatalogEntryMode = "catalog" | "ai-import";
const NO_COMPANY_STANDARD = "__no_company_standard__";

export interface MasterCatalogDialogProps {
  client?: AuthoredVnextMasterClient;
  /** Called once, only after an explicit Apply of a currently published master. */
  onApply: (published: MasterRevisionEnvelope) => void | Promise<void>;
  /** Clears the current application selection so authored templates can run alone. */
  onClear?: () => void | Promise<void>;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  /** The current application selection, restored when this dialog is cancelled. */
  selectedMasterId?: string | null;
  entryMode?: MasterCatalogEntryMode;
  analysisSourceFile?: File | null;
  onAnalysisSourceFileChange?: (file: File) => void;
  resumeAnalysisId?: string | null;
  onAnalysisIdChange?: (analysisId: string | null) => void;
}

const defaultClient = new AuthoredVnextMasterClient();

function mergeReviewedContract(
  base: DeckMasterContractV1,
  edited: DeckMasterContractV1,
  compiled: DeckMasterContractV1,
): DeckMasterContractV1 {
  const merged = structuredClone(compiled);
  const changed = (left: unknown, right: unknown) =>
    JSON.stringify(left) !== JSON.stringify(right);
  for (const field of [
    "name", "semanticVersion", "canvas", "brandAssets", "tokens",
    "fontManifest", "furniture", "semanticStyles", "fitPolicy", "titleStressCorpus",
  ] as const) {
    if (changed(edited[field], base[field])) {
      (merged as unknown as Record<string, unknown>)[field] = structuredClone(edited[field]);
    }
  }
  for (const kind of ["cover", "section", "standard", "longTitle"] as const) {
    if (changed(edited.layouts[kind], base.layouts[kind])) {
      merged.layouts[kind] = structuredClone(edited.layouts[kind]);
    }
  }
  const semantic = new Map((compiled.semanticElements ?? []).map((item) => [item.id, item]));
  const baseSemantic = new Map((base.semanticElements ?? []).map((item) => [item.id, item]));
  for (const item of edited.semanticElements ?? []) {
    const original = baseSemantic.get(item.id);
    if (!original || changed(item, original)) semantic.set(item.id, structuredClone(item));
  }
  for (const id of baseSemantic.keys()) {
    if (!(edited.semanticElements ?? []).some((item) => item.id === id)) semantic.delete(id);
  }
  merged.semanticElements = [...semantic.values()];
  const custom = new Map((compiled.customLayouts ?? []).map((item) => [item.id, item]));
  const baseCustom = new Map((base.customLayouts ?? []).map((item) => [item.id, item]));
  for (const item of edited.customLayouts ?? []) {
    const original = baseCustom.get(item.id);
    if (!original || changed(item, original)) custom.set(item.id, structuredClone(item));
  }
  for (const id of baseCustom.keys()) {
    if (!(edited.customLayouts ?? []).some((item) => item.id === id)) custom.delete(id);
  }
  merged.customLayouts = [...custom.values()];
  return merged;
}

export default function MasterCatalogDialog({
  client = defaultClient,
  onApply,
  onClear,
  onOpenChange,
  open,
  selectedMasterId = null,
  entryMode = "catalog",
  analysisSourceFile = null,
  onAnalysisSourceFileChange,
  resumeAnalysisId = null,
  onAnalysisIdChange,
}: MasterCatalogDialogProps) {
  const t = useTranslations("authoredVnext.master.catalog");
  const ta = useTranslations("authoredVnext.master.analysis");
  const dialogRef = useRef<HTMLDivElement>(null);
  const applyingRef = useRef(false);
  const [view, setView] = useState<CatalogView>("catalog");
  const [entries, setEntries] = useState<MasterCatalogSummary[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [pendingMasterId, setPendingMasterId] = useState<string | null>(null);
  const [activeMaster, setActiveMaster] = useState<MasterRevisionEnvelope | null>(null);
  const [designerMode, setDesignerMode] = useState<DesignerMode>("new");
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [objective, setObjective] = useState<AiMasterPurpose>("faithful");
  const [analysis, setAnalysis] = useState<AiMasterAnalysis | null>(null);
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const [analysisPatchPending, setAnalysisPatchPending] = useState(0);
  const [analysisFailureMessage, setAnalysisFailureMessage] = useState<string | null>(null);
  const [analysisSaving, setAnalysisSaving] = useState(false);
  const [analysisOverlay, setAnalysisOverlay] = useState(false);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);
  const analysisRef = useRef<AiMasterAnalysis | null>(null);
  const editorContractRef = useRef<DeckMasterContractV1 | null>(null);
  const editorContractBaseRef = useRef<DeckMasterContractV1 | null>(null);
  const editorContractDirtyRef = useRef(false);
  const patchQueueRef = useRef<Promise<void>>(Promise.resolve());
  const patchEpochRef = useRef(0);

  const clearAnalysisEditorState = useCallback(() => {
    patchEpochRef.current += 1;
    analysisRef.current = null;
    editorContractRef.current = null;
    editorContractBaseRef.current = null;
    editorContractDirtyRef.current = false;
    setAnalysis(null);
    setAnalysisPatchPending(0);
    setAnalysisFailureMessage(null);
    setAnalysisOverlay(false);
    setSelectedCandidateId(null);
  }, []);

  const load = useCallback(async (cursor?: string) => {
    const isMore = Boolean(cursor);
    isMore ? setLoadingMore(true) : setLoading(true);
    setError(null);
    try {
      const page = await client.listMasters({ cursor, limit: 24 });
      setEntries((current) => isMore ? [...current, ...page.items] : page.items);
      setNextCursor(page.nextCursor);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "catalog_request_failed");
    } finally {
      isMore ? setLoadingMore(false) : setLoading(false);
    }
  }, [client]);

  useEffect(() => {
    if (!open) return;
    setView(entryMode === "ai-import" ? "objective" : "catalog");
    setPendingMasterId(selectedMasterId ?? (onClear ? NO_COMPANY_STANDARD : null));
    setActiveMaster(null);
    setAnalysis(null);
    setAnalysisPatchPending(0);
    setAnalysisFailureMessage(null);
    analysisRef.current = null;
    editorContractRef.current = null;
    setError(null);
    if (entryMode === "catalog") {
      void load();
    } else if (resumeAnalysisId) {
      setAnalysisBusy(true);
      void client.getAiAnalysis(resumeAnalysisId).then((restored) => {
        analysisRef.current = restored;
        editorContractRef.current = structuredClone(restored.masterRevision?.contract ?? restored.contractDraft);
        editorContractBaseRef.current = structuredClone(restored.masterRevision?.contract ?? restored.contractDraft);
        editorContractDirtyRef.current = false;
        setAnalysis(restored);
        setObjective(restored.purpose);
        setSelectedCandidateId(restored.layoutCandidates[0]?.id ?? null);
        setActiveMaster(restored.masterRevision ?? null);
        setDesignerMode("edit");
        setView("designer");
      }).catch((cause) => {
        if (
          cause instanceof MasterApiError &&
          cause.status === 404 &&
          cause.code === "authored_vnext_ai_master_analysis_not_found"
        ) {
          onAnalysisIdChange?.(null);
          setView("objective");
          setError(null);
          return;
        }
        setError(cause instanceof Error ? cause.message : "analysis_restore_failed");
      }).finally(() => setAnalysisBusy(false));
    }
    dialogRef.current?.focus();
  }, [client, entryMode, load, onAnalysisIdChange, onClear, open, resumeAnalysisId, selectedMasterId]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !applyingRef.current) onOpenChange(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onOpenChange, open]);

  const queueAnalysisUpdate = (
    next: AiMasterAnalysis,
    request: { decisions?: AiMasterCandidateDecision[]; layoutActions?: AiMasterLayoutAction[] },
    recalculating = false,
  ) => {
    const epoch = ++patchEpochRef.current;
    if (recalculating) {
      setAnalysisFailureMessage(null);
      setError(null);
      setAnalysisPatchPending((pending) => pending + 1);
    }
    analysisRef.current = next;
    setAnalysis(next);
    patchQueueRef.current = patchQueueRef.current
      .catch(() => undefined)
      .then(async () => {
        const persisted = await client.updateAiAnalysis(next.analysisId, request);
        if (patchEpochRef.current === epoch) {
          const base = editorContractBaseRef.current;
          const edited = editorContractRef.current;
          const editorContract = editorContractDirtyRef.current && base && edited
            ? mergeReviewedContract(base, edited, persisted.contractDraft)
            : structuredClone(persisted.contractDraft);
          editorContractRef.current = editorContract;
          editorContractBaseRef.current = structuredClone(persisted.contractDraft);
          analysisRef.current = persisted;
          setAnalysis(persisted);
          setAnalysisFailureMessage(null);
          setError(null);
          setSelectedCandidateId((currentId) =>
            currentId && persisted.layoutCandidates.some((candidate) => candidate.id === currentId)
              ? currentId
              : persisted.layoutCandidates[0]?.id ?? null,
          );
        }
      })
      .catch((cause) => {
        if (patchEpochRef.current !== epoch) return;
        const message = cause instanceof Error ? cause.message : "analysis_update_failed";
        setAnalysisFailureMessage(message);
        setError(message);
      })
      .finally(() => {
        if (recalculating && patchEpochRef.current === epoch) {
          setAnalysisPatchPending((pending) => Math.max(0, pending - 1));
        }
      });
  };

  const startAnalysis = async () => {
    if (!analysisSourceFile || analysisBusy) {
      if (!analysisSourceFile) setError("analysis_source_required");
      return;
    }
    setAnalysisBusy(true);
    setAnalysisFailureMessage(null);
    setError(null);
    try {
      const result = await client.analyzePptx(analysisSourceFile, objective);
      analysisRef.current = result;
      editorContractRef.current = structuredClone(result.contractDraft);
      editorContractBaseRef.current = structuredClone(result.contractDraft);
      editorContractDirtyRef.current = false;
      setAnalysis(result);
      setSelectedCandidateId(result.layoutCandidates[0]?.id ?? null);
      setActiveMaster(result.masterRevision ?? null);
      setDesignerMode("edit");
      setView("designer");
      onAnalysisIdChange?.(result.analysisId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "analysis_request_failed");
    } finally {
      setAnalysisBusy(false);
    }
  };

  const updateRepeatedDecision = (
    candidateId: string,
    decision: RepeatedTextDecision,
  ) => {
    const current = analysisRef.current;
    if (!current) return;
    const candidate = current.repeatedElements.find((item) => item.id === candidateId);
    const existing = current.decisions.find((item) => item.candidateId === candidateId);
    const action = decision === "input" ? "variable" : decision === "ai-zone" ? "aiZone" : decision;
    const nextDecision: AiMasterCandidateDecision = {
      ...(existing ?? {}),
      candidateId,
      action,
      ...(action === "aiZone" && !existing?.instruction
        ? {
            instruction: `Find the document value for ${candidate?.role ?? candidate?.text ?? candidateId} and show it here.`,
            exampleText: candidate?.text ?? "",
            emptyBehavior: "hidden" as const,
            maxChars: 80,
          }
        : {}),
    };
    const decisions = [
      ...current.decisions.filter((item) => item.candidateId !== candidateId),
      nextDecision,
    ];
    queueAnalysisUpdate({ ...current, decisions }, { decisions });
  };

  const updateAiBinding = (candidateId: string, binding: PptAnalysisAiBinding) => {
    const current = analysisRef.current;
    if (!current) return;
    const existing = current.decisions.find((item) => item.candidateId === candidateId);
    const decisions = [
      ...current.decisions.filter((item) => item.candidateId !== candidateId),
      {
        ...(existing ?? {}),
        candidateId,
        action: "aiZone" as const,
        instruction: binding.instruction,
        exampleText: binding.exampleText,
        emptyBehavior: binding.emptyBehavior,
        fallbackText: binding.fallbackText,
        maxChars: binding.maxChars,
      },
    ];
    const next = { ...current, decisions };
    if (!binding.instruction.trim() || (binding.emptyBehavior === "fallback" && !binding.fallbackText?.trim())) {
      analysisRef.current = next;
      setAnalysis(next);
      return;
    }
    queueAnalysisUpdate(next, { decisions });
  };

  const updateLayout = (
    layoutIds: string[],
    action: "merge" | "split" | "exclude" | "include",
    slideNumbers: number[] = [],
  ) => {
    const current = analysisRef.current;
    if (!current) return;
    const layoutAction: AiMasterLayoutAction = {
      action,
      layoutIds,
      slideNumbers,
    };
    queueAnalysisUpdate(
      current,
      { layoutActions: [layoutAction] },
      true,
    );
  };

  const reanalyze = () => {
    const current = analysisRef.current;
    if (!current) return;
    const layoutAction: AiMasterLayoutAction = {
      action: "reanalyze",
      layoutIds: current.layoutCandidates.map((item) => item.id),
      slideNumbers: [],
    };
    queueAnalysisUpdate(current, { layoutActions: [layoutAction] }, true);
  };

  const runAnalysisValidation = async () => {
    const current = analysisRef.current;
    if (!current || analysisBusy) return;
    setAnalysisBusy(true);
    setAnalysisFailureMessage(null);
    setError(null);
    try {
      await patchQueueRef.current;
      const validated = await client.validateAiAnalysis(current.analysisId);
      analysisRef.current = validated;
      setAnalysis(validated);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "analysis_validation_failed";
      setAnalysisFailureMessage(message);
      setError(message);
    } finally {
      setAnalysisBusy(false);
    }
  };

  const saveAnalysisReview = async (): Promise<MasterRevisionEnvelope | null> => {
    const current = analysisRef.current;
    if (!current || analysisSaving) return null;
    setAnalysisSaving(true);
    setError(null);
    try {
      await patchQueueRef.current;
      const validated = await client.validateAiAnalysis(current.analysisId);
      const currentHead = current.masterRevision;
      const baseContract = editorContractBaseRef.current;
      const editedContract = editorContractRef.current;
      const reviewedContract = editorContractDirtyRef.current && baseContract && editedContract
        ? mergeReviewedContract(baseContract, editedContract, validated.contractDraft)
        : validated.contractDraft;
      const saved = await client.saveAiAnalysis(
        validated.analysisId,
        currentHead
          ? {
              masterId: currentHead.masterId,
              expectedRevision: currentHead.revision,
              expectedDigest: currentHead.digest,
              contract: reviewedContract,
            }
          : { contract: reviewedContract },
      );
      if (!saved.masterRevision) throw new Error("analysis_save_missing_master_revision");
      const revision = saved.masterRevision;
      const persisted = { ...saved, masterRevision: revision };
      analysisRef.current = persisted;
      editorContractRef.current = structuredClone(revision.contract ?? persisted.contractDraft);
      editorContractBaseRef.current = structuredClone(revision.contract ?? persisted.contractDraft);
      editorContractDirtyRef.current = false;
      setAnalysis(persisted);
      setActiveMaster(revision);
      onAnalysisIdChange?.(persisted.analysisId);
      return revision;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "analysis_save_failed");
      return null;
    } finally {
      setAnalysisSaving(false);
    }
  };

  const analysisReview: PptAnalysisReviewProps | undefined = analysis
    ? {
        analysisId: analysis.analysisId,
        sourceDigest: analysis.source.digest,
        status: analysis.status,
        objective: analysis.purpose as PptAnalysisObjective,
        summary: {
          slideCount: analysis.source.slideCount,
          designFamilies: [String(analysis.summary.designFamilies)],
          fonts: analysis.fonts.map((item) => item.family ?? "").filter(Boolean),
          colors: analysis.colors.map((item) => item.hex ?? "").filter(Boolean),
          repeatedElements: analysis.repeatedElements.map((item) => item.role),
          aiZoneCount: analysis.summary.aiZoneCandidateCount,
          lowConfidenceCount: analysis.summary.lowConfidenceCount,
        },
        layouts: analysis.layoutCandidates.map((layout, index) => {
          const custom = analysis.contractDraft.customLayouts?.find((item) => item.name === layout.name)
            ?? analysis.contractDraft.customLayouts?.[index];
          const selector = ["cover", "section", "standard", "longTitle"].includes(layout.kind)
            ? layout.kind
            : custom?.id ?? "longTitle";
          return {
            id: layout.id,
            selector,
            name: layout.name,
            kind: layout.kind,
            confidence: layout.confidence,
            slideNumbers: layout.slideNumbers,
            representativeSlide: layout.representativeSlide,
            evidence: layout.slideNumbers.map((slideNumber) => ({ slideNumber })),
            differences: layout.differences.map((item) => item.message),
            elementRoles: layout.slots.map((slot) => slot.role),
            excluded: layout.excluded,
          };
        }),
        repeatedText: analysis.repeatedElements
          .filter((candidate) => candidate.type === "text")
          .map((candidate) => {
            const decision = analysis.decisions.find((item) => item.candidateId === candidate.id);
            const reviewDecision = decision?.action === "variable"
              ? "input"
              : decision?.action === "aiZone"
                ? "ai-zone"
                : decision?.action === "pending"
                  ? undefined
                  : decision?.action;
            return {
              id: candidate.id,
              text: candidate.text ?? candidate.role,
              label: candidate.role,
              confidence: candidate.confidence,
              slideNumbers: [...new Set(candidate.evidence.map((item) => item.slideNumber))],
              decision: reviewDecision,
              ...(decision?.action === "aiZone" && decision.instruction && decision.emptyBehavior
                ? { aiBinding: {
                    instruction: decision.instruction,
                    exampleText: decision.exampleText ?? candidate.text ?? "",
                    emptyBehavior: decision.emptyBehavior,
                    fallbackText: decision.fallbackText,
                    maxChars: decision.maxChars,
                  } }
                : {}),
            };
          }),
        validationFindings: [
          ...(analysis.validation?.issues.map((issue, index) => ({
            id: `${issue.code}:${issue.slideNumber ?? index}`,
            severity: issue.severity,
            code: issue.code,
            message: issue.message,
            slideNumbers: issue.slideNumber ? [issue.slideNumber] : undefined,
          })) ?? []),
          ...analysis.warnings.map((message, index) => ({
            id: `analysis-warning:${index}`,
            severity: "info" as const,
            message,
          })),
        ],
        overlay: analysisOverlay,
        sourceOverlay: (() => {
          const active = analysis.layoutCandidates.find((item) => item.id === selectedCandidateId)
            ?? analysis.layoutCandidates[0];
          const boxes = [
            ...analysis.repeatedElements.flatMap((candidate) =>
              candidate.evidence
                .filter((item) => item.slideNumber === active?.representativeSlide)
                .map((item) => ({ ...item.bounds, label: candidate.role, id: `${candidate.id}:${item.slideNumber}` })),
            ),
            ...(active?.slots.map((slot) => ({ ...slot.bounds, label: slot.role, id: slot.id })) ?? []),
          ];
          return (
            <div className="relative aspect-video w-[min(92%,1200px)] border border-cyan-300 bg-slate-900/20">
              {boxes.map((box) => (
                <span
                  className="absolute border-2 border-cyan-300 bg-cyan-300/10 text-[10px] font-bold text-white"
                  key={box.id}
                  style={{
                    left: `${(box.x / analysis.source.widthPx) * 100}%`,
                    top: `${(box.y / analysis.source.heightPx) * 100}%`,
                    width: `${(box.width / analysis.source.widthPx) * 100}%`,
                    height: `${(box.height / analysis.source.heightPx) * 100}%`,
                  }}
                >
                  {box.label}
                </span>
              ))}
            </div>
          );
        })(),
        isReanalyzing: analysisBusy || analysisPatchPending > 0,
        isSaving: analysisSaving,
        failureMessage: analysisFailureMessage ?? undefined,
        onObjectiveChange: (next) => setObjective(next),
        onSelectLayout: setSelectedCandidateId,
        onLayoutAction: updateLayout,
        onOverlayChange: setAnalysisOverlay,
        onRepeatedTextDecision: updateRepeatedDecision,
        onAiBindingChange: updateAiBinding,
        onReanalyze: reanalyze,
        onRunValidation: () => void runAnalysisValidation(),
        onSave: () => void saveAnalysisReview(),
      }
    : undefined;

  if (!open) return null;

  const openNew = () => {
    clearAnalysisEditorState();
    setActiveMaster(null);
    setDesignerMode("new");
    setView("designer");
  };
  const openExisting = async (entry: MasterCatalogSummary) => {
    clearAnalysisEditorState();
    setLoading(true);
    setError(null);
    try {
      const master = entry.hasDraft
        ? await client.getHead(entry.masterId)
        : await client.getPublished(entry.masterId);
      if (!master) throw new Error("master_not_found");
      setActiveMaster(master);
      setDesignerMode("edit");
      setView("designer");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "catalog_request_failed");
    } finally {
      setLoading(false);
    }
  };
  const applyPending = async () => {
    if (!pendingMasterId || applyingRef.current) return;
    applyingRef.current = true;
    setError(null);
    try {
      if (pendingMasterId === NO_COMPANY_STANDARD) {
        await onClear?.();
        onOpenChange(false);
        return;
      }
      // Re-resolve the pointer at the commit boundary so stale cards cannot pin an old revision.
      const published = await client.getPublished(pendingMasterId);
      await onApply(published);
      onOpenChange(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "catalog_apply_failed");
    } finally {
      applyingRef.current = false;
    }
  };

  return (
    <div
      aria-labelledby="master-catalog-dialog-title"
      aria-modal="true"
      className="fixed inset-0 z-50 bg-slate-950/45 p-0 sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !applyingRef.current) onOpenChange(false);
      }}
      role="dialog"
    >
      <div className="flex h-full w-full flex-col overflow-hidden bg-slate-50 shadow-2xl sm:mx-auto sm:max-w-[1600px] sm:rounded-2xl" ref={dialogRef} tabIndex={-1}>
        <header className="flex shrink-0 items-center justify-between gap-4 border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
          <div>
            <h1 className="text-lg font-bold text-slate-950" id="master-catalog-dialog-title">{t(view === "catalog" ? "title" : "designerTitle")}</h1>
            <p className="text-xs text-slate-600">{t(view === "catalog" ? "description" : "designerDescription")}</p>
          </div>
          <button aria-label={t("close")} className="rounded p-2 text-xl leading-none text-slate-600 hover:bg-slate-100" onClick={() => { if (!applyingRef.current) onOpenChange(false); }} type="button">×</button>
        </header>
        {error && <p className="mx-4 mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">{error === "authored_vnext_ai_master_legacy_ppt_unsupported" ? ta("legacyPptUnsupported") : error === "analysis_source_required" ? ta("sourceRequired") : t("error", { code: error })}</p>}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {view === "objective" ? (
            <section className="mx-auto max-w-4xl space-y-5 px-4 py-8 sm:px-8">
              <PptAnalysisObjectivePicker
                review={{
                  status: analysisBusy ? "analyzing" : "idle",
                  objective,
                  layouts: [],
                  onObjectiveChange: setObjective,
                }}
              />
              <div className="flex items-center justify-end gap-3">
                <button
                  className="rounded border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700"
                  onClick={() => onOpenChange(false)}
                  type="button"
                >
                  {t("cancel")}
                </button>
                <button
                  className="rounded bg-violet-700 px-4 py-2 text-sm font-bold text-white disabled:opacity-40"
                  data-testid="ppt-ai-analysis-start"
                  disabled={!analysisSourceFile || analysisBusy}
                  onClick={() => void startAnalysis()}
                  type="button"
                >
                  {analysisBusy ? ta("analyzing") : ta("start")}
                </button>
              </div>
              <div
                className={`flex flex-wrap items-center justify-between gap-3 rounded border p-3 text-sm ${analysisSourceFile ? "border-emerald-200 bg-emerald-50 text-emerald-950" : "border-amber-200 bg-amber-50 text-amber-950"}`}
                role="status"
              >
                <span>
                  {analysisSourceFile
                    ? ta("sourceSelected", { name: analysisSourceFile.name })
                    : ta("sourceRequired")}
                </span>
                {onAnalysisSourceFileChange ? (
                  <label className="cursor-pointer rounded border border-current bg-white px-3 py-2 text-sm font-bold hover:bg-slate-50">
                    <input
                      accept=".ppt,.pptx,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation"
                      className="sr-only"
                      data-testid="ppt-ai-source-input"
                      onChange={(event) => {
                        const file = event.currentTarget.files?.[0];
                        if (file) onAnalysisSourceFileChange(file);
                        event.currentTarget.value = "";
                      }}
                      type="file"
                    />
                    {analysisSourceFile ? ta("replaceSource") : ta("selectSource")}
                  </label>
                ) : null}
              </div>
            </section>
          ) : view === "designer" ? (
            <div data-testid={analysis ? "ppt-ai-analysis-review" : undefined}>
              <MasterSetupGate
                analysisReview={analysisReview}
                createNew={analysis ? !activeMaster : designerMode === "new"}
                client={client}
                embedded
                externalContract={analysis ? editorContractRef.current ?? undefined : undefined}
                initialContract={analysis && !activeMaster ? analysis.contractDraft : undefined}
                initialMaster={activeMaster}
                key={analysis
                  ? `analysis:${analysis.analysisId}:${activeMaster?.revision ?? 0}`
                  : `${designerMode}:${activeMaster?.masterId ?? "new"}:${activeMaster?.revision ?? 0}`}
                onBack={() => { clearAnalysisEditorState(); setView("catalog"); void load(); }}
                onContractChange={(contract) => {
                  editorContractRef.current = contract;
                  editorContractDirtyRef.current = true;
                }}
                onPublishComplete={(published) => {
                  clearAnalysisEditorState();
                  setPendingMasterId(published.masterId);
                  setView("catalog");
                  void load();
                }}
                prepareDraft={analysis ? saveAnalysisReview : undefined}
              />
            </div>
          ) : (
            <section className="mx-auto max-w-[1320px] space-y-5 px-4 py-6 sm:px-8">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div><h2 className="text-xl font-bold text-slate-950">{t("listTitle")}</h2><p className="mt-1 text-sm text-slate-600">{t("listDescription")}</p></div>
                <button className="rounded bg-slate-950 px-4 py-3 text-sm font-bold text-white hover:bg-slate-800" onClick={openNew} type="button">{t("newMaster")}</button>
              </div>
              {loading && entries.length === 0 ? <p className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">{t("loading")}</p> : null}
              {!loading && entries.length === 0 ? <p className="rounded-xl border border-dashed border-slate-300 bg-white p-8 text-sm text-slate-600">{t("empty")}</p> : null}
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {onClear ? (
                  <article
                    className={`rounded-xl border bg-white p-4 shadow-sm ${pendingMasterId === NO_COMPANY_STANDARD ? "border-violet-500 ring-2 ring-violet-100" : "border-slate-200"}`}
                    data-testid="no-company-standard-option"
                  >
                    <button
                      aria-pressed={pendingMasterId === NO_COMPANY_STANDARD}
                      className="flex h-full min-h-52 w-full flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 px-6 py-8 text-center hover:border-violet-300 hover:bg-violet-50/50"
                      onClick={() => setPendingMasterId(NO_COMPANY_STANDARD)}
                      type="button"
                    >
                      <span className="flex h-12 w-12 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm">
                        <Ban className="h-5 w-5" aria-hidden="true" />
                      </span>
                      <h3 className="mt-4 font-bold text-slate-950">{t("noneTitle")}</h3>
                      <p className="mt-2 text-sm leading-6 text-slate-600">{t("noneDescription")}</p>
                    </button>
                  </article>
                ) : null}
                {entries.map((entry) => <MasterCatalogCard entry={entry} key={entry.masterId} onEdit={() => void openExisting(entry)} onSelect={() => setPendingMasterId(entry.masterId)} pending={pendingMasterId === entry.masterId} />)}
              </div>
              {nextCursor && <button className="rounded border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 disabled:opacity-50" disabled={loadingMore} onClick={() => void load(nextCursor)} type="button">{loadingMore ? t("loading") : t("loadMore")}</button>}
            </section>
          )}
        </div>
        {view === "catalog" && <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-slate-200 bg-white px-4 py-3 sm:px-6"><p className="text-xs text-slate-600">{pendingMasterId === NO_COMPANY_STANDARD ? t("pendingNoStandard") : pendingMasterId ? t("pendingReady") : t("pendingNone")}</p><div className="flex gap-2"><button className="rounded border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50" onClick={() => { if (!applyingRef.current) onOpenChange(false); }} type="button">{t("cancel")}</button><button className="rounded bg-violet-700 px-4 py-2 text-sm font-bold text-white disabled:opacity-40" disabled={!pendingMasterId || applyingRef.current} onClick={() => void applyPending()} type="button">{pendingMasterId === NO_COMPANY_STANDARD ? t("applyNone") : t("apply")}</button></div></footer>}
      </div>
    </div>
  );
}
