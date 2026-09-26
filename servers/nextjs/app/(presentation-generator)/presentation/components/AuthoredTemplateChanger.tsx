"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  Layers3,
  LayoutTemplate,
  Loader2,
  Search,
  Sparkles,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useDispatch } from "react-redux";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { notify } from "@/components/ui/sonner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { clearPresentationData } from "@/store/slices/presentationGeneration";
import { clearHistory } from "@/store/slices/undoRedoSlice";
import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import type { MasterCatalogSummary } from "@/lib/authored-vnext/master/contracts";
import { companyStandardDisplayName } from "@/lib/authored-vnext/master/selection-storage";
import AuthoredStylesApi, {
  DEFAULT_AUTHORED_STYLE,
  canonicalAuthoredStyleId,
} from "../../services/api/authored";
import type {
  AuthoredStyleCategory,
  AuthoredStyleSummary,
} from "../../services/api/authored";
import AuthoredStyleCard from "../../outline/components/AuthoredStyleCard";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import {
  getWorkspaceTimeline,
  postWorkspaceTemplateApplication,
} from "../../services/api/workspace";
import type { WorkspaceCommandDispatcher } from "../workspace";
import type {
  WorkspaceAuthoredStyleIntent,
  WorkspaceMasterIntent,
} from "../workspace/types";
import type { PresentationMasterPin } from "../utils/masterPresentation";

const POLL_INTERVAL_MS = 2_000;
const GENERATION_TIMEOUT_MS = 30 * 60 * 1_000;
const MAX_POLL_ERRORS = 4;
const masterClient = new AuthoredVnextMasterClient();

const CATEGORY_LABELS: Record<AuthoredStyleCategory, string> = {
  general: "범용",
  business: "비즈니스",
  technology: "기술",
  research: "리서치",
  editorial: "에디토리얼",
  creative: "크리에이티브",
};

function normalizeSearchText(value: string) {
  return value.normalize("NFKC").trim().toLocaleLowerCase("ko-KR");
}

function createIdempotencyKey(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function targetPresentationUrl(presentationId: string): string {
  return `/presentation?id=${encodeURIComponent(presentationId)}&workspace_staging=template`;
}

interface WorkspaceTemplateAttempt {
  signature: string;
  idempotencyKey: string;
  promise: Promise<void>;
  settled: boolean;
}

export default function AuthoredTemplateChanger({
  presentationId,
  currentStyleId,
  currentMasterPin = null,
  disabled = false,
  onWorkspaceCommand,
  navigateToPresentation,
}: {
  presentationId: string;
  currentStyleId?: string | null;
  currentMasterPin?: PresentationMasterPin | null;
  disabled?: boolean;
  onWorkspaceCommand?: WorkspaceCommandDispatcher;
  navigateToPresentation?: (targetUrl: string) => void;
}) {
  const t = useTranslations("presentationTemplateChanger");
  const categoryLabels: Record<AuthoredStyleCategory, string> = {
    general: t("categoryGeneral"), business: t("categoryBusiness"),
    technology: t("categoryTechnology"), research: t("categoryResearch"),
    editorial: t("categoryEditorial"), creative: t("categoryCreative"),
  };
  const [open, setOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"template" | "master">("template");
  const [styles, setStyles] = useState<AuthoredStyleSummary[]>([]);
  const effectiveCurrentStyleId = canonicalAuthoredStyleId(
    styles,
    currentStyleId || DEFAULT_AUTHORED_STYLE.id,
  );
  const [selectedStyleId, setSelectedStyleId] = useState(
    effectiveCurrentStyleId
  );
  const [masters, setMasters] = useState<MasterCatalogSummary[]>([]);
  const [masterIntent, setMasterIntent] = useState<WorkspaceMasterIntent>({
    intent: "keep",
  });
  const [selectedMasterName, setSelectedMasterName] = useState<string | null>(
    null,
  );
  const [searchQuery, setSearchQuery] = useState("");
  const [isLoadingStyles, setIsLoadingStyles] = useState(false);
  const [isLoadingMasters, setIsLoadingMasters] = useState(false);
  const [masterLoadError, setMasterLoadError] = useState<string | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [visionQa, setVisionQa] = useState(false);
  const workspaceAttemptRef = useRef<WorkspaceTemplateAttempt | null>(null);
  const workspaceRetryKeysRef = useRef(new Map<string, string>());
  const router = useRouter();
  const dispatch = useDispatch();

  useEffect(() => {
    if (!open || styles.length > 0) return;

    let cancelled = false;
    setIsLoadingStyles(true);
    AuthoredStylesApi.getStyles()
      .then((nextStyles) => {
        if (cancelled) return;
        setStyles(nextStyles);
        setSelectedStyleId(canonicalAuthoredStyleId(nextStyles, effectiveCurrentStyleId));
      })
      .catch((error) => {
        if (cancelled) return;
        console.error("Failed to load AI-authored templates", error);
        notify.error(
          t("stylesLoadFailed"),
          t("tryAgainLater")
        );
      })
      .finally(() => {
        if (!cancelled) setIsLoadingStyles(false);
      });

    return () => {
      cancelled = true;
    };
  }, [effectiveCurrentStyleId, open, styles.length]);

  useEffect(() => {
    if (!open || masters.length > 0 || isLoadingMasters) return;

    const controller = new AbortController();
    setIsLoadingMasters(true);
    setMasterLoadError(null);
    masterClient
      .listMasters({ limit: 100 }, controller.signal)
      .then((page) => setMasters(page.items))
      .catch((error) => {
        if (controller.signal.aborted) return;
        console.error("Failed to load PPT masters", error);
        setMasterLoadError(t("mastersLoadFailed"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoadingMasters(false);
      });

    return () => controller.abort();
  }, [masters.length, open]);

  const filteredStyles = useMemo(() => {
    const query = normalizeSearchText(searchQuery);
    if (!query) return styles;
    return styles.filter((style) =>
      normalizeSearchText(
        [
          style.name,
          style.description,
          CATEGORY_LABELS[style.category],
          ...style.tags,
          ...style.use_cases,
        ].join(" ")
      ).includes(query)
    );
  }, [searchQuery, styles]);

  const authoredStyleIntent = useMemo<WorkspaceAuthoredStyleIntent>(
    () =>
      selectedStyleId === effectiveCurrentStyleId
        ? { intent: "keep" }
        : { intent: "set", styleId: selectedStyleId },
    [effectiveCurrentStyleId, selectedStyleId],
  );
  const currentStyleName =
    styles.find((style) => style.id === effectiveCurrentStyleId)?.name ||
    (effectiveCurrentStyleId === DEFAULT_AUTHORED_STYLE.id
      ? DEFAULT_AUTHORED_STYLE.name
      : effectiveCurrentStyleId);
  const selectedStyleName =
    styles.find((style) => style.id === selectedStyleId)?.name ||
    (selectedStyleId === DEFAULT_AUTHORED_STYLE.id
      ? DEFAULT_AUTHORED_STYLE.name
      : selectedStyleId);
  const currentMasterName =
    (currentMasterPin?.name
      ? companyStandardDisplayName(currentMasterPin.name, currentMasterPin.masterId)
      : undefined) ||
    (masters.find((entry) => entry.masterId === currentMasterPin?.masterId)?.name
      ? companyStandardDisplayName(
          masters.find((entry) => entry.masterId === currentMasterPin?.masterId)!.name,
          currentMasterPin?.masterId,
        )
      : undefined) ||
    (currentMasterPin ? t("currentCompanyStandard") : t("noCompanyStandard"));
  const nextMasterName =
    masterIntent.intent === "keep"
      ? currentMasterName
      : masterIntent.intent === "clear"
        ? t("noCompanyStandard")
        : selectedMasterName || t("selectedCompanyStandard");
  const styleChanged = authoredStyleIntent.intent === "set";
  const masterChanged =
    masterIntent.intent === "set" ||
    (masterIntent.intent === "clear" && currentMasterPin !== null);
  const hasDesignChange = styleChanged || masterChanged;
  const ctaLabel =
    styleChanged && masterChanged
      ? t("recreateWithDesign")
      : masterChanged
        ? t("recreateWithStandard")
        : t("recreateWithTemplate");

  const selectMaster = (entry: MasterCatalogSummary) => {
    if (entry.publishedRevision === null || entry.publishedDigest === null) return;
    if (
      currentMasterPin?.masterId === entry.masterId &&
      currentMasterPin.revision === entry.publishedRevision &&
      currentMasterPin.digest === entry.publishedDigest
    ) {
      setMasterIntent({ intent: "keep" });
      setSelectedMasterName(null);
      return;
    }
    setMasterIntent({
      intent: "set",
      pin: {
        masterId: entry.masterId,
        revision: entry.publishedRevision,
        digest: entry.publishedDigest,
      },
    });
    setSelectedMasterName(companyStandardDisplayName(entry.name, entry.masterId));
  };

  const resetSelections = () => {
    setActiveTab("template");
    setSelectedStyleId(effectiveCurrentStyleId);
    setMasterIntent({ intent: "keep" });
    setSelectedMasterName(null);
    setSearchQuery("");
  };

  const changeTemplate = async () => {
    if (!selectedStyleId || !hasDesignChange) return;

    if (onWorkspaceCommand) {
      const signature = JSON.stringify({
        authoredStyle: authoredStyleIntent,
        resolvedStyleId: selectedStyleId,
        master: masterIntent,
        resolvedMasterPin:
          masterIntent.intent === "keep" ? currentMasterPin : undefined,
        visionQa,
      });
      const existing = workspaceAttemptRef.current;
      if (existing?.signature === signature && !existing.settled) {
        return existing.promise;
      }

      const prompt = styleChanged
        ? masterChanged
          ? `${selectedStyleName} AI 저작 템플릿과 ${nextMasterName} 조합으로 새 자료를 만들어 줘`
          : `${selectedStyleName} AI 저작 템플릿으로 새 자료를 만들어 줘`
        : `${nextMasterName} 설정으로 새 자료를 만들어 줘`;
      // A replay after an ambiguous failure must reuse the original key so the
      // server can return the already-created target instead of allocating a
      // second presentation. Only a changed template choice starts a new key.
      const idempotencyKey =
        existing?.signature === signature
          ? existing.idempotencyKey
          : workspaceRetryKeysRef.current.get(signature) ?? createIdempotencyKey();
      workspaceRetryKeysRef.current.set(signature, idempotencyKey);
      setIsGenerating(true);
      setOpen(false);
      let attempt: WorkspaceTemplateAttempt;
      const promise = (async () => {
        const sourceTimeline = await getWorkspaceTimeline(presentationId, {
          limit: 1,
        });
        if (sourceTimeline.revision === undefined) {
          throw new Error(t("latestRevisionUnknown"));
        }
        const receipt = await postWorkspaceTemplateApplication(presentationId, {
          sourceRevision: sourceTimeline.revision,
          prompt,
          authoredStyle: authoredStyleIntent,
          master: masterIntent,
          visionQa,
          idempotencyKey,
        });
        const targetUrl = targetPresentationUrl(receipt.targetPresentationId);
        if (navigateToPresentation) navigateToPresentation(targetUrl);
        else window.location.assign(targetUrl);
      })()
        .catch((error) => {
          console.error("Error confirming AI-authored template", error);
          notify.error(
            t("changeFailed"),
            error instanceof Error
              ? error.message
              : t("requestFailed"),
          );
          setOpen(true);
        })
        .finally(() => {
          setIsGenerating(false);
          if (workspaceAttemptRef.current === attempt) {
            attempt.settled = true;
          }
        });
      attempt = { signature, idempotencyKey, promise, settled: false };
      workspaceAttemptRef.current = attempt;
      return promise;
    }

    if (isGenerating) return;

    if (masterIntent.intent !== "keep" || authoredStyleIntent.intent !== "set") {
      notify.error(
        t("designChangeUnavailable"),
        t("combinedChangeUnavailable"),
      );
      return;
    }

    setIsGenerating(true);
    try {
      const started =
        await PresentationGenerationApi.retemplateAuthoredPresentation(
          presentationId,
          {
            authored_style: authoredStyleIntent.styleId,
            vision_qa: visionQa,
          }
        );
      const taskId = started?.id;
      if (!taskId) throw new Error(t("startFailed"));

      notify.success(
        t("changeStarted"),
        t("sourcePreserved")
      );

      const deadline = Date.now() + GENERATION_TIMEOUT_MS;
      let pollErrors = 0;
      while (Date.now() < deadline) {
        await new Promise((resolve) =>
          window.setTimeout(resolve, POLL_INTERVAL_MS)
        );

        try {
          const task =
            await PresentationGenerationApi.getGenerationStatus(taskId);
          pollErrors = 0;
          if (task?.status === "completed") {
            const newPresentationId = task?.data?.presentation_id;
            if (!newPresentationId) {
              throw new Error(t("generatedNotFound"));
            }

            dispatch(clearPresentationData());
            dispatch(clearHistory());
            setOpen(false);
            router.push(`/presentation?id=${newPresentationId}`);
            return;
          }
          if (task?.status === "error") {
            throw new Error(
              task?.message || t("generationFailed")
            );
          }
        } catch (error) {
          pollErrors += 1;
          if (pollErrors >= MAX_POLL_ERRORS) throw error;
        }
      }

      throw new Error(t("generationTimedOut"));
    } catch (error) {
      console.error("Error changing AI-authored template", error);
      notify.error(
        t("changeFailed"),
        error instanceof Error
          ? error.message
          : t("changeError")
      );
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!isGenerating) {
          if (nextOpen) resetSelections();
          setOpen(nextOpen);
        }
      }}
    >
      <DialogTrigger asChild>
        <Button
          type="button"
          variant="toolbar"
          size="toolbar"
          disabled={disabled}
          data-testid="template-change-trigger"
        >
          <LayoutTemplate className="h-4 w-4" />
          {t("designChange")}
        </Button>
      </DialogTrigger>

      <DialogContent className="flex h-[90vh] max-h-[90vh] max-w-6xl flex-col overflow-hidden p-0">
        <DialogHeader className="px-7 pb-2 pt-7">
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-[#5141E5]" />
            {t("designChange")}
          </DialogTitle>
          <DialogDescription className="leading-6">
            {t("description")}
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col px-7 py-3">
          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as "template" | "master")}
            className="flex min-h-0 flex-1 flex-col"
          >
            <TabsList className="grid h-12 w-full shrink-0 grid-cols-2 rounded-xl border border-gray-200 bg-gray-50 p-1">
              <TabsTrigger
                value="template"
                data-testid="design-tab-template"
                className="h-10 rounded-lg font-semibold data-[state=active]:bg-white data-[state=active]:text-[#5141E5] data-[state=active]:shadow-sm"
              >
                {t("templateStep")}
                {styleChanged ? <span className="ml-2 text-xs">{t("changed")}</span> : null}
              </TabsTrigger>
              <TabsTrigger
                value="master"
                data-testid="design-tab-master"
                className="h-10 rounded-lg font-semibold data-[state=active]:bg-white data-[state=active]:text-[#5141E5] data-[state=active]:shadow-sm"
              >
                {t("standardStep")}
                {masterChanged ? <span className="ml-2 text-xs">{t("changed")}</span> : null}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="template" className="mt-4 min-h-0 flex-1 overflow-hidden data-[state=active]:flex data-[state=active]:flex-col">
          <section aria-labelledby="authored-template-heading" className="flex h-full min-h-0 flex-col">
            <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 id="authored-template-heading" className="font-semibold text-gray-950">
                  {t("templateStep")}
                </h3>
                <p className="mt-1 text-xs text-gray-500">
                  {t("templateDescription")}
                </p>
              </div>
              <label className="relative block sm:w-80">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                <input
                  type="search"
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.target.value)}
                  placeholder={t("searchPlaceholder")}
                  className="h-10 w-full rounded-xl border border-[#E3E4E8] bg-white pl-10 pr-4 text-sm outline-none focus:border-[#5141E5] focus:ring-2 focus:ring-[#5141E5]/15"
                />
              </label>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto pr-1" data-testid="authored-template-scroll">
              {isLoadingStyles ? (
                <div className="flex min-h-40 items-center justify-center gap-2 text-sm text-gray-600">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  {t("loadingTemplates")}
                </div>
              ) : (
                <div
                  role="radiogroup"
                  aria-label={t("templateGroup")}
                  data-testid="authored-template-grid"
                  className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
                >
                  {filteredStyles.map((style) => (
                    <AuthoredStyleCard
                      key={style.id}
                      style={style}
                      categoryLabel={categoryLabels[style.category]}
                      isSelected={style.id === selectedStyleId}
                      isTabStop={style.id === selectedStyleId}
                      onSelect={setSelectedStyleId}
                    />
                  ))}
                </div>
              )}
              {!isLoadingStyles && filteredStyles.length === 0 && (
                <p className="py-10 text-center text-sm text-gray-500">
                  {t("noMatchingTemplates")}
                </p>
              )}
            </div>
          </section>
            </TabsContent>

            <TabsContent value="master" className="mt-4 min-h-0 flex-1 overflow-hidden data-[state=active]:flex data-[state=active]:flex-col">
          <section
            aria-labelledby="ppt-master-heading"
            className="flex h-full min-h-0 flex-col"
          >
            <div className="mb-3">
              <h3 id="ppt-master-heading" className="font-semibold text-gray-950">
                {t("standardStep")}
              </h3>
              <p className="mt-1 text-xs text-gray-500">
                {t("standardDescription")}
              </p>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto pr-1" data-testid="ppt-master-scroll">
              <div
                role="radiogroup"
                aria-label={t("companyStandard")}
                className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
                data-testid="ppt-master-grid"
              >
              <button
                type="button"
                role="radio"
                aria-checked={masterIntent.intent === "keep"}
                data-testid="master-choice-keep"
                onClick={() => {
                  setMasterIntent({ intent: "keep" });
                  setSelectedMasterName(null);
                }}
                className={`relative rounded-xl border p-4 text-left transition ${
                  masterIntent.intent === "keep"
                    ? "border-[#5141E5] bg-[#F6F4FF] ring-2 ring-[#5141E5]/10"
                    : "border-gray-200 bg-white hover:border-gray-300"
                }`}
              >
                <span className="flex items-center gap-2 font-semibold text-gray-950">
                  <Layers3 className="h-4 w-4 text-[#5141E5]" />
                  {t("keepCurrentStandard")}
                </span>
                <span className="mt-2 block text-sm text-gray-600">{currentMasterName}</span>
                {masterIntent.intent === "keep" && (
                  <Check className="absolute right-3 top-3 h-4 w-4 text-[#5141E5]" />
                )}
              </button>

              <button
                type="button"
                role="radio"
                aria-checked={masterIntent.intent === "clear"}
                data-testid="master-choice-clear"
                onClick={() => {
                  setMasterIntent({ intent: "clear" });
                  setSelectedMasterName(null);
                }}
                className={`relative rounded-xl border p-4 text-left transition ${
                  masterIntent.intent === "clear"
                    ? "border-[#5141E5] bg-[#F6F4FF] ring-2 ring-[#5141E5]/10"
                    : "border-gray-200 bg-white hover:border-gray-300"
                }`}
              >
                <span className="font-semibold text-gray-950">{t("clearStandard")}</span>
                <span className="mt-2 block text-sm text-gray-600">
                  {t("clearStandardDescription")}
                </span>
                {masterIntent.intent === "clear" && (
                  <Check className="absolute right-3 top-3 h-4 w-4 text-[#5141E5]" />
                )}
              </button>

              {masters
                .filter(
                  (entry) =>
                    entry.publishedRevision !== null &&
                    entry.publishedDigest !== null,
                )
                .map((entry) => {
                  const isCurrent =
                    currentMasterPin?.masterId === entry.masterId &&
                    currentMasterPin.revision === entry.publishedRevision &&
                    currentMasterPin.digest === entry.publishedDigest;
                  const selected =
                    masterIntent.intent === "set" &&
                    masterIntent.pin.masterId === entry.masterId &&
                    masterIntent.pin.revision === entry.publishedRevision &&
                    masterIntent.pin.digest === entry.publishedDigest;
                  return (
                    <button
                      key={entry.masterId}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      data-testid={`master-choice-${entry.masterId}`}
                      onClick={() => selectMaster(entry)}
                      className={`relative rounded-xl border p-4 text-left transition ${
                        selected
                          ? "border-[#5141E5] bg-[#F6F4FF] ring-2 ring-[#5141E5]/10"
                          : "border-gray-200 bg-white hover:border-gray-300"
                      }`}
                    >
                      <span className="font-semibold text-gray-950">
                        {companyStandardDisplayName(entry.name, entry.masterId)}
                      </span>
                      <span className="mt-2 block text-sm text-gray-600">
                        {t("masterLayoutCount", { count: entry.layoutCount, font: entry.primaryFont })}
                      </span>
                      {isCurrent ? (
                        <span className="mt-2 inline-flex rounded-full bg-blue-50 px-2 py-1 text-xs font-semibold text-blue-700">
                          {t("currentlyApplied")}
                        </span>
                      ) : null}
                      {selected && (
                        <Check className="absolute right-3 top-3 h-4 w-4 text-[#5141E5]" />
                      )}
                    </button>
                  );
                })}
              </div>
              {isLoadingMasters && (
                <p className="mt-3 flex items-center gap-2 text-sm text-gray-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("loadingStandards")}
                </p>
              )}
              {masterLoadError && (
                <p className="mt-3 text-sm text-amber-700">{t("masterLoadErrorWithFallback", { error: masterLoadError })}</p>
              )}
            </div>
          </section>
            </TabsContent>
          </Tabs>

          <div className="mt-4 shrink-0 rounded-xl border border-[#DDD9FF] bg-[#F8F7FF] px-4 py-3 text-sm" data-testid="design-combination-summary">
            <span className="font-semibold text-gray-950">{t("currentCombination")}</span>
            <span className="ml-2 text-gray-700" data-testid="current-design-combination">
              {currentStyleName} + {currentMasterName}
            </span>
            <span className="mx-3 text-[#5141E5]" aria-hidden="true">→</span>
            <span className="font-semibold text-gray-950">{t("afterChange")}</span>
            <span className="ml-2 text-gray-700" data-testid="next-design-combination">
              {selectedStyleName} + {nextMasterName}
            </span>
          </div>
        </div>

        <DialogFooter className="items-center border-t bg-white px-7 py-5 sm:justify-between">
          <label className="flex cursor-pointer items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={visionQa}
              disabled={isGenerating}
              onChange={(event) => setVisionQa(event.target.checked)}
              className="h-4 w-4 rounded border-gray-300 accent-[#5141E5]"
            />
            {t("addQualityReview")}
            <span className="text-xs text-gray-500">{t("qualityReviewSlow")}</span>
          </label>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={isGenerating}
              onClick={() => setOpen(false)}
            >
              {t("cancel")}
            </Button>
            <Button
              type="button"
              disabled={
                !selectedStyleId ||
                !hasDesignChange ||
                isLoadingStyles ||
                isGenerating
              }
              data-testid="template-change-confirm"
              onClick={changeTemplate}
            >
              {!onWorkspaceCommand && isGenerating && <Loader2 className="h-4 w-4 animate-spin" />}
              {!onWorkspaceCommand && isGenerating
                ? t("generating")
                : ctaLabel}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
