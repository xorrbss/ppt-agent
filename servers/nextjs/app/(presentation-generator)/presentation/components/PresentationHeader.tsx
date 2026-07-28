"use client";
import { Button } from "@/components/ui/button";
import {
  Play,
  Loader2,
  Redo2,
  Undo2,
  RotateCcw,
  ArrowRightFromLine,
  ArrowUpRight,
  Pencil,
  Check,
  X,
  AlertTriangle,
} from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import Image from "next/image";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { useDispatch, useSelector } from "react-redux";

import { RootState } from "@/store/store";
import { notify } from "@/components/ui/sonner";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { usePresentationUndoRedo } from "../hooks/PresentationUndoRedo";
import ToolTip from "@/components/ToolTip";
import {
  clearPresentationData,
  updateTitle,
} from "@/store/slices/presentationGeneration";
import { clearHistory } from "@/store/slices/undoRedoSlice";
import { Separator } from "@/components/ui/separator";
import type { PresentationExportQualityReport } from "@/lib/presentation-export-quality";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import ThemeSelector from "./ThemeSelector";
import VersionHistoryPopover from "./VersionHistoryPopover";
import SharePopover from "./SharePopover";
import { DEFAULT_THEMES } from "../../(dashboard)/theme/components/ThemePanel/constants";
import ThemeApi from "../../services/api/theme";
import { Theme } from "../../services/api/types";
import MarkdownRenderer from "@/components/MarkDownRender";
import { cn } from "@/lib/utils";
import AuthoredTemplateChanger from "./AuthoredTemplateChanger";
import AuthoredQualityReview from "./AuthoredQualityReview";

const MAX_EXPORT_TITLE_LENGTH = 40;
type PptxExportMode = "fidelity" | "hybrid";

function getAuthoredStyleId(theme: unknown): string | null {
  if (!theme || typeof theme !== "object") return null;
  const style = (theme as Record<string, unknown>).style;
  return typeof style === "string" ? style : null;
}

const buildSafeExportFileName = (
  rawTitle: string | null | undefined,
  extension: "pdf" | "pptx"
) => {
  const normalizedTitle = (rawTitle || "presentation").trim();
  const titleWithoutExtension = normalizedTitle.replace(/\.(pdf|pptx)$/i, "");

  let safeBase = titleWithoutExtension
    // Replace all punctuation/special chars (including dots) with dashes
    .replace(/[^a-zA-Z0-9\s_-]+/g, "-")
    // Replace whitespace with single dashes
    .replace(/\s+/g, "-")
    // Collapse repeated separators
    .replace(/[-_]{2,}/g, "-")
    // Trim separators from both ends
    .replace(/^[-_]+|[-_]+$/g, "");

  if (!safeBase) {
    safeBase = "presentation";
  }

  if (safeBase.length > MAX_EXPORT_TITLE_LENGTH) {
    safeBase = safeBase
      .slice(0, MAX_EXPORT_TITLE_LENGTH)
      .replace(/[-_]+$/g, "");
  }

  if (!safeBase) {
    safeBase = "presentation";
  }

  return `${safeBase}.${extension}`;
};

const PresentationHeader = ({
  presentation_id,
  isPresentationSaving,
  currentSlide,
  onReload,
  isAuthoredDeck = false,
}: {
  presentation_id: string;
  isPresentationSaving: boolean;
  currentSlide?: number;
  onReload?: () => void;
  isAuthoredDeck?: boolean;
}) => {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const [isExporting, setIsExporting] = useState(false);
  const [themes, setThemes] = useState<Theme[]>([]);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [isRegenerateConfirmOpen, setIsRegenerateConfirmOpen] = useState(false);
  const [exportQuality, setExportQuality] =
    useState<PresentationExportQualityReport | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);
  const exportTriggerRef = useRef<HTMLButtonElement>(null);
  /** Avoid committing on blur when Save/Cancel was used (focus/click ordering) */
  const titleBlurIntentRef = useRef<"none" | "save" | "cancel">("none");

  const pathname = usePathname();
  const dispatch = useDispatch();

  const { presentationData, isStreaming } = useSelector(
    (state: RootState) => state.presentationGeneration
  );

  useEffect(() => {
    if (isAuthoredDeck) return;

    let cancelled = false;
    const load = async () => {
      try {
        const [customThemes] = await Promise.all([ThemeApi.getThemes()]);
        if (!cancelled) setThemes([...customThemes, ...DEFAULT_THEMES]);
      } catch (e: any) {
        if (!cancelled) {
          notify.error("테마를 불러올 수 없습니다", e?.message || "테마를 불러오지 못했습니다.");
        }
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [isAuthoredDeck]);

  const { onUndo, onRedo, canUndo, canRedo } = usePresentationUndoRedo();

  useEffect(() => {
    if (isEditingTitle) {
      titleInputRef.current?.focus();
      titleInputRef.current?.select();
    }
  }, [isEditingTitle]);

  // Restore focus to the export trigger once an export finishes. The trigger is
  // `disabled` while `isExporting`, so focusing it inside the export handler could
  // no-op before React re-enabled the button; running here (after the enabling
  // re-render commits) guarantees keyboard focus returns to the trigger.
  const wasExportingRef = useRef(false);
  useEffect(() => {
    if (wasExportingRef.current && !isExporting) {
      exportTriggerRef.current?.focus();
    }
    wasExportingRef.current = isExporting;
  }, [isExporting]);

  const beginTitleEdit = () => {
    if (isStreaming || !presentationData) return;
    setDraftTitle(presentationData.title || "");
    setIsEditingTitle(true);
  };

  const commitTitleEdit = () => {
    if (!presentationData) {
      setIsEditingTitle(false);
      return;
    }
    const trimmed = draftTitle.trim();
    const next = trimmed || presentationData.title || "발표자료";
    if (next !== presentationData.title) {
      dispatch(updateTitle(next));
      trackEvent(MixpanelEvent.Presentation_Title_Updated, {
        pathname,
        presentation_id,
        previous_title_length: (presentationData.title || "").length,
        next_title_length: next.length,
      });
    }
    setIsEditingTitle(false);
  };

  const cancelTitleEdit = () => {
    setDraftTitle(presentationData?.title || "");
    setIsEditingTitle(false);
  };

  const handleTitleBlur = () => {
    queueMicrotask(() => {
      const intent = titleBlurIntentRef.current;
      titleBlurIntentRef.current = "none";
      if (intent === "cancel" || intent === "save") return;
      commitTitleEdit();
    });
  };

  const onTitleSaveMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    titleBlurIntentRef.current = "save";
  };

  const onTitleCancelMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    titleBlurIntentRef.current = "cancel";
  };

  const exportViaIpc = async (
    format: "pptx" | "pdf",
    title: string
  ): Promise<void> => {
    if (!window.electron?.exportPresentation) {
      throw new Error("Electron export bridge is unavailable");
    }
    const result = await window.electron.exportPresentation(
      presentation_id,
      title,
      format
    );
    if (!result?.success) {
      throw new Error(result?.message || "Export failed");
    }
  };

  const handleExportPptx = async (
    pptxMode?: PptxExportMode,
    fontEmbedding = false
  ) => {
    if (isStreaming || isExporting) return;

    let exportToastId: string | number | undefined;
    try {
      trackEvent(MixpanelEvent.Presentation_Export_Started, {
        pathname,
        presentation_id,
        format: "pptx",
        slide_count: presentationData?.slides?.length || 0,
      });
      exportToastId = notify.loading(
        "PPTX 내보내는 중",
        "발표자료를 내보내고 있습니다. 잠시 시간이 걸릴 수 있습니다."
      );
      setIsExporting(true);
      // Save the presentation data before exporting
      await PresentationGenerationApi.updatePresentationContent(
        presentationData
      );
      const safePptxFileName = buildSafeExportFileName(
        presentationData?.title,
        "pptx"
      );
      const safePptxTitle = safePptxFileName.replace(/\.pptx$/i, "");
      // The desktop bridge has no pptxMode argument. Authored exports use the
      // web API so both fidelity and hybrid modes are explicit; adaptive keeps IPC.
      if (window.electron?.exportPresentation && !pptxMode) {
        await exportViaIpc("pptx", safePptxTitle);
      } else {
        const requestBody = {
          format: "pptx" as const,
          id: presentation_id,
          title: safePptxTitle,
          ...(pptxMode ? { pptxMode, fontEmbedding } : {}),
        };
        const response = await fetch("/api/export-presentation", {
          method: "POST",
          body: JSON.stringify(requestBody),
        });

        if (!response.ok) {
          throw new Error("PPTX 내보내기에 실패했습니다.");
        }

        const payload = (await response.json()) as {
          path?: string;
          quality?: PresentationExportQualityReport;
        };
        const pptxPath = payload.path;
        if (!pptxPath) {
          throw new Error("내보내기 경로를 받지 못했습니다.");
        }

        downloadLink(pptxPath, safePptxFileName);
        if (payload.quality) setExportQuality(payload.quality);
      }
      notify.success(
        "내보내기 완료",
        "PPTX 파일이 다운로드되었습니다.",
        { id: exportToastId }
      );
    } catch (error) {
      console.error("Export failed:", error);
      notify.error(
        "내보내기 실패",
        "발표자료를 내보내는 데 문제가 발생했습니다. 다시 시도해 주세요.",
        exportToastId !== undefined ? { id: exportToastId } : undefined
      );
    } finally {
      setIsExporting(false);
    }
  };

  const handleExportPdf = async () => {
    if (isStreaming || isExporting) return;

    let exportToastId: string | number | undefined;
    try {
      trackEvent(MixpanelEvent.Presentation_Export_Started, {
        pathname,
        presentation_id,
        format: "pdf",
        slide_count: presentationData?.slides?.length || 0,
      });
      exportToastId = notify.loading(
        "PDF 내보내는 중",
        "발표자료를 내보내고 있습니다. 잠시 시간이 걸릴 수 있습니다."
      );
      setIsExporting(true);
      // Save the presentation data before exporting
      await PresentationGenerationApi.updatePresentationContent(
        presentationData
      );
      const safePdfFileName = buildSafeExportFileName(
        presentationData?.title,
        "pdf"
      );
      const safePdfTitle = safePdfFileName.replace(/\.pdf$/i, "");
      if (window.electron?.exportPresentation) {
        await exportViaIpc("pdf", safePdfTitle);
      } else {
        const response = await fetch("/api/export-presentation", {
          method: "POST",
          body: JSON.stringify({
            format: "pdf",
            id: presentation_id,
            title: safePdfTitle,
          }),
        });

        if (response.ok) {
          const { path: pdfPath } = await response.json();
          downloadLink(pdfPath, safePdfFileName);
        } else {
          throw new Error("PDF 내보내기에 실패했습니다.");
        }
      }
      notify.success(
        "내보내기 완료",
        "PDF 파일이 다운로드되었습니다.",
        { id: exportToastId }
      );
    } catch (err) {
      console.error(err);
      notify.error(
        "내보내기 실패",
        "발표자료를 내보내는 데 문제가 발생했습니다. 다시 시도해 주세요.",
        exportToastId !== undefined ? { id: exportToastId } : undefined
      );
    } finally {
      setIsExporting(false);
    }
  };
  const handleReGenerate = () => {
    setIsRegenerateConfirmOpen(false);
    dispatch(clearPresentationData());
    dispatch(clearHistory());
    trackEvent(MixpanelEvent.Presentation_Regenerated, {
      pathname,
      presentation_id,
      slide_count: presentationData?.slides?.length || 0,
    });
    // regenerate=true forces the stream to re-run generation; without it the
    // stream now replays existing slides idempotently (so a refresh can't wipe edits).
    router.push(`/presentation?id=${presentation_id}&stream=true&regenerate=true`);
  };
  const downloadLink = (path: string, fileName: string) => {
    const link = document.createElement("a");
    link.href = path;
    link.download = fileName;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const closeExportOptions = () => {
    setOpen(false);
    requestAnimationFrame(() => exportTriggerRef.current?.focus());
  };

  const ExportOptions = ({ mobile }: { mobile: boolean }) => (
    <div
      className={`rounded-[18px] max-md:mt-4 ${mobile ? "" : "bg-white"} p-5`}
      aria-label="내보내기 형식 선택"
      role="group"
    >
      <p className="text-sm font-medium text-[#19001F]">다음 형식으로 내보내기</p>
      <div className="my-[18px] h-[1px] bg-[#E8E8E8]" />
      <div className="space-y-3">
        {isAuthoredDeck && (
          <>
            <Button
              type="button"
              onClick={() => {
                closeExportOptions();
                void handleExportPptx("fidelity");
              }}
              disabled={isExporting || isStreaming === true}
              data-testid="authored-export-fidelity"
              variant="ghost"
              className="min-h-11 w-full items-start justify-between whitespace-normal rounded-lg px-2 py-2 text-left text-xs text-black hover:bg-[#F6F6F9] disabled:cursor-not-allowed"
              aria-label="PPTX로 내보내기, 디자인 그대로"
            >
              <span className="min-w-0">
                <span className="block font-medium">PPTX · 디자인 그대로</span>
                <span className="mt-1 block text-[11px] font-normal leading-snug text-[#5F5B66]">
                  슬라이드를 이미지로 유지합니다.
                </span>
              </span>
              <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            </Button>
            <Button
              type="button"
              onClick={() => {
                closeExportOptions();
                void handleExportPptx("hybrid");
              }}
              disabled={isExporting || isStreaming === true}
              data-testid="authored-export-hybrid"
              variant="ghost"
              className="min-h-11 w-full items-start justify-between whitespace-normal rounded-lg px-2 py-2 text-left text-xs text-black hover:bg-[#F6F6F9] disabled:cursor-not-allowed"
              aria-label="PPTX로 내보내기, 텍스트 편집 가능"
            >
              <span className="min-w-0">
                <span className="block font-medium">PPTX · 텍스트 편집 가능</span>
                <span className="mt-1 block text-[11px] font-normal leading-snug text-[#5F5B66]">
                  텍스트와 일부 요소를 편집할 수 있습니다.
                </span>
              </span>
              <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            </Button>
            <Button
              type="button"
              onClick={() => {
                closeExportOptions();
                void handleExportPptx("hybrid", true);
              }}
              disabled={isExporting || isStreaming === true}
              data-testid="authored-export-hybrid-embedded"
              variant="ghost"
              className="min-h-11 w-full items-start justify-between whitespace-normal rounded-lg border border-amber-200 bg-amber-50/60 px-2 py-2 text-left text-xs text-black hover:bg-amber-50 disabled:cursor-not-allowed"
              aria-label="PPTX로 내보내기, 편집 가능 및 글꼴 포함"
            >
              <span className="min-w-0">
                <span className="block font-medium">
                  PPTX · 편집 가능 + 글꼴 포함
                </span>
                <span
                  className="mt-1 block text-[11px] font-normal leading-snug text-amber-900"
                  role="note"
                >
                  명시적 opt-in입니다. 서버에서 허용된 글꼴만 포함하며 파일
                  크기가 크게 늘 수 있습니다. 실패 시 호환 글꼴로 대체됩니다.
                </span>
              </span>
              <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            </Button>
          </>
        )}
        <Button
          onClick={() => {
            closeExportOptions();
            void handleExportPdf();
          }}
          disabled={isExporting || isStreaming === true}
          data-testid="export-pdf"
          variant="ghost"
          className={`  rounded-none px-0 w-full text-xs flex justify-start text-black hover:bg-transparent ${
            mobile ? "bg-white py-6 border-none rounded-lg" : ""
          }`}
        >
          PDF
          <ArrowUpRight className="w-3.5 h-3.5" />
        </Button>
        {!isAuthoredDeck && <Button
          onClick={() => {
            closeExportOptions();
            void handleExportPptx();
          }}
          disabled={isExporting || isStreaming === true}
          data-testid="export-pptx"
          variant="ghost"
          className={`w-full flex px-0 justify-start text-xs text-black hover:bg-transparent  ${
            mobile ? "bg-white py-6" : ""
          }`}
        >
          PPTX
          <ArrowUpRight className="w-3.5 h-3.5" />
        </Button>}
      </div>
    </div>
  );

  const titleBlock = (
    <div
      className={cn(
        "min-w-0 flex-1 transition-[box-shadow] duration-200",
        isEditingTitle && "relative z-[60]"
      )}
    >
      {isEditingTitle ? (
        <div className="flex w-full max-w-[450px] items-stretch gap-0.5 rounded-[14px] border border-[#E4E2EB] bg-white pl-3.5 pr-1 py-1 shadow-[0_2px_12px_rgba(17,3,31,0.06)] ring-2 ring-[#5141e5]/15">
          <input
            ref={titleInputRef}
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            onBlur={handleTitleBlur}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                titleBlurIntentRef.current = "save";
                commitTitleEdit();
              } else if (e.key === "Escape") {
                e.preventDefault();
                titleBlurIntentRef.current = "cancel";
                cancelTitleEdit();
              }
            }}
            placeholder="발표자료 제목"
            className="min-w-0 flex-1 bg-transparent py-2 pr-2 font-unbounded text-base leading-tight text-[#101323] placeholder:text-[#101323]/35 outline-none border-0 focus:ring-0"
            aria-label="발표자료 제목"
          />
          <div className="flex shrink-0 items-center gap-0.5 border-l border-[#EDECEC] pl-1 ml-0.5">
            <ToolTip content="저장 · Enter">
              <button
                type="button"
                onMouseDown={onTitleSaveMouseDown}
                onClick={commitTitleEdit}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-[#5141e5] hover:bg-[#5141e5]/10 transition-colors"
                aria-label="제목 저장"
              >
                <Check className="h-4 w-4" strokeWidth={2.25} />
              </button>
            </ToolTip>
            <ToolTip content="취소 · Esc">
              <button
                type="button"
                onMouseDown={onTitleCancelMouseDown}
                onClick={cancelTitleEdit}
                className="flex h-8 w-8 items-center justify-center rounded-lg text-[#101323]/55 hover:bg-[#F6F6F9] hover:text-[#101323] transition-colors"
                aria-label="제목 편집 취소"
              >
                <X className="h-4 w-4" strokeWidth={2.25} />
              </button>
            </ToolTip>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={beginTitleEdit}
          disabled={isStreaming || !presentationData}
          className={cn(
            "group/title flex w-full min-w-0 items-center gap-2.5 rounded-[14px] px-3 py-2 text-left -mx-3 transition-colors",
            "hover:bg-[#F6F6F9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141e5] focus-visible:ring-offset-2",
            "disabled:pointer-events-none disabled:opacity-100 disabled:hover:bg-transparent"
          )}
        >
          <h2 className="min-w-0 flex-1 font-unbounded text-lg leading-snug text-[#101323]">
            <MarkdownRenderer
              content={presentationData?.title || "발표자료"}
              className="mb-0 min-w-0 overflow-hidden text-ellipsis line-clamp-1 text-sm text-[#101323] prose-p:my-0 prose-headings:my-0"
            />
          </h2>
          {presentationData && !isStreaming && (
            <Pencil
              className="h-3.5 w-3.5 shrink-0 text-[#101323]/40 transition-all duration-200 group-hover/title:text-[#5141e5] opacity-80 sm:opacity-0 sm:group-hover/title:opacity-100 group-hover/title:opacity-100"
              aria-hidden
            />
          )}
        </button>
      )}
    </div>
  );

  return (
    <>
      <div className="sticky top-0 z-50 flex w-full min-w-0 items-center justify-between gap-4 bg-white px-4 py-[18px] font-syne shadow-sm">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <Image
            onClick={() => {
              router.push("/dashboard");
            }}
            src="/logo-with-bg.png"
            alt=""
            width={40}
            height={40}
            className="h-10 w-10 shrink-0 cursor-pointer object-contain"
          />
          {presentationData && !isStreaming && !isEditingTitle ? (
            <ToolTip content="발표자료 이름 변경">{titleBlock}</ToolTip>
          ) : (
            titleBlock
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2.5">
          {isPresentationSaving && (
            <div className="flex items-center gap-2">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            </div>
          )}
          {presentationData && isAuthoredDeck ? (
            <>
              <AuthoredTemplateChanger
                presentationId={presentation_id}
                currentStyleId={getAuthoredStyleId(presentationData.theme)}
                disabled={isStreaming || isPresentationSaving}
              />
              <AuthoredQualityReview
                presentationId={presentation_id}
                currentSlide={currentSlide}
                slideCount={presentationData.slides?.length || 0}
                disabled={isStreaming || isPresentationSaving}
                onReload={onReload}
              />
            </>
          ) : (
            presentationData?.slides &&
            !presentationData.slides?.[0]?.layout?.includes("custom") && (
              <ThemeSelector
                current_theme={presentationData?.theme || {}}
                themes={themes}
              />
            )
          )}

          <div className="flex items-center gap-2 bg-[#F6F6F9] px-3.5 h-[38px] border border-[#EDECEC] rounded-[80px]">
            <ToolTip content="발표자료 재생성">
              <button
                type="button"
                onClick={() => setIsRegenerateConfirmOpen(true)}
                className="group"
              >
                <RotateCcw className="w-3.5 h-3.5 text-[#101323] group-hover:text-[#5141e5] duration-300" />
              </button>
            </ToolTip>
            <Separator orientation="vertical" className="h-4" />
            <ToolTip content="실행 취소">
              <button
                disabled={!canUndo}
                className=" disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer group"
                onClick={() => {
                  onUndo();
                }}
              >
                <Undo2 className="w-3.5 h-3.5 text-[#101323] group-hover:text-[#5141e5] duration-300" />
              </button>
            </ToolTip>
            <Separator orientation="vertical" className="h-4" />
            <ToolTip content="다시 실행">
              <button
                disabled={!canRedo}
                className=" disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer group"
                onClick={() => {
                  onRedo();
                }}
              >
                <Redo2 className="w-3.5 h-3.5 text-[#101323] group-hover:text-[#5141e5] duration-300" />
              </button>
            </ToolTip>
            <Separator orientation="vertical" className="h-4 w-[2px]" />
            <ToolTip content="발표 시작">
              <button
                onClick={() => {
                  const to = `?id=${presentation_id}&mode=present&slide=${
                    currentSlide || 0
                  }`;
                  trackEvent(MixpanelEvent.Presentation_Mode_Entered, {
                    pathname,
                    presentation_id,
                    slide_index: currentSlide || 0,
                    slide_count: presentationData?.slides?.length || 0,
                  });
                  trackEvent(MixpanelEvent.Navigation, { from: pathname, to });
                  router.push(to);
                }}
                disabled={
                  isStreaming ||
                  !presentationData?.slides ||
                  presentationData?.slides.length === 0
                }
                className="cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed group"
              >
                <Play className="w-3.5 h-3.5 text-[#101323] group-hover:text-[#5141e5] duration-300" />
              </button>
            </ToolTip>
            <Separator orientation="vertical" className="h-4" />
            <VersionHistoryPopover
              presentationId={presentation_id}
              disabled={isStreaming === true}
              onRestored={() => onReload?.()}
            />
          </div>

          <SharePopover
            presentationId={presentation_id}
            disabled={isStreaming === true}
          />

          <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
              <button
                ref={exportTriggerRef}
                type="button"
                className="flex  items-center gap-[7px] px-[18px] py-[11px] rounded-[53px] text-sm font-semibold text-[#101323]"
                style={{
                  background:
                    "linear-gradient(270deg, #D5CAFC 2.4%, #E3D2EB 27.88%, #F4DCD3 69.23%, #FDE4C2 100%)",
                }}
                disabled={isExporting || isStreaming === true}
                data-testid="export-trigger"
                aria-label={isExporting ? "내보내는 중" : "내보내기 형식 선택"}
              >
                {isExporting ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  "내보내기"
                )}{" "}
                <ArrowRightFromLine className="w-3.5 h-3.5" />
              </button>
            </PopoverTrigger>
            <PopoverContent
              align="end"
              className="w-[min(22rem,calc(100vw-2rem))] max-h-[calc(100vh-2rem)] overflow-y-auto rounded-[18px] space-y-2 p-0"
            >
              <ExportOptions mobile={false} />
            </PopoverContent>
          </Popover>
          {isExporting && (
            <span className="sr-only" role="status" aria-live="polite">
              발표자료를 내보내는 중입니다.
            </span>
          )}
        </div>
      </div>
      <Dialog
        open={exportQuality !== null}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) setExportQuality(null);
        }}
      >
        <DialogContent
          className="w-[420px] rounded-2xl sm:max-w-[420px]"
          data-testid="export-quality-dialog"
        >
          <DialogHeader>
            <DialogTitle>PPTX 편집 품질</DialogTitle>
            <DialogDescription>
              전체 {exportQuality?.totalSlides ?? 0}장 · 편집 요소 포함{" "}
              {exportQuality?.editableSlides ?? 0}장 · 이미지 fallback{" "}
              {exportQuality?.imageFallbackSlides ?? 0}장
            </DialogDescription>
          </DialogHeader>
          {exportQuality &&
            (exportQuality.status !== "fully-editable" ||
              exportQuality.imageFallbackSlides > 0 ||
              exportQuality.rasterFallbackElements > 0) && (
            <div
              className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900"
              role="alert"
            >
              일부 콘텐츠는 이미지로 남아 완전 편집형이 아닙니다.
            </div>
          )}
          {exportQuality && (
            <div className="space-y-2 text-sm text-gray-600">
              <p>
                네이티브 텍스트 {exportQuality.nativeTextElements}개 · 도형{" "}
                {exportQuality.nativeShapeElements}개 · 그룹{" "}
                {exportQuality.nativeGroupElements ?? 0}개 · 이미지{" "}
                {exportQuality.nativeImageElements}개 · 잔여 raster{" "}
                {exportQuality.rasterFallbackElements}개
              </p>
              {exportQuality.slides.some(
                (slide) => slide.fallbackReasons.length > 0
              ) && (
                <div className="max-h-36 overflow-y-auto rounded-lg bg-gray-50 p-3">
                  {exportQuality.slides
                    .filter((slide) => slide.fallbackReasons.length > 0)
                    .flatMap((slide) =>
                      (slide.fallbackElements?.length ?? 0) > 0
                        ? (slide.fallbackElements ?? []).map((element) => (
                            <p
                              key={`${slide.slideNumber}:${element.elementId}`}
                            >
                              {slide.slideNumber}장 · {element.elementId} (
                              {element.candidateKind}):{" "}
                              {element.reasons.join(", ")}
                            </p>
                          ))
                        : [
                            <p key={slide.slideNumber}>
                              {slide.slideNumber}장:{" "}
                              {slide.fallbackReasons.join(", ")}
                            </p>,
                          ]
                    )}
                </div>
              )}
              <p className="text-xs text-gray-500">
                {exportQuality.fontEmbeddingStatus?.applied
                  ? `OOXML 폰트 임베딩 ${exportQuality.fontEmbeddingStatus.embeddedFontFiles}개가 적용되었습니다.`
                  : exportQuality.fontEmbeddingStatus?.requested
                    ? "폰트 임베딩 opt-in을 요청했지만 적용되지 않아 호환 typeface/fallback을 사용했습니다."
                    : "폰트 임베딩은 기본 off이며 호환 typeface/fallback을 사용합니다."}
              </p>
            </div>
          )}
          <DialogFooter>
            <Button type="button" onClick={() => setExportQuality(null)}>
              확인
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        open={isRegenerateConfirmOpen}
        onOpenChange={setIsRegenerateConfirmOpen}
      >
        <DialogContent className="w-[360px] rounded-2xl border-0 p-0 shadow-2xl sm:max-w-[360px]">
          <DialogHeader className="items-center px-6 pb-4 pt-6 text-center">
            <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-50">
              <AlertTriangle className="h-6 w-6 text-red-500" />
            </div>
            <DialogTitle className="text-lg font-semibold text-[#191919]">
              발표자료를 재생성할까요?
            </DialogTitle>
            <DialogDescription className="text-sm leading-relaxed text-gray-500">
              현재 슬라이드가 새로 생성된 버전으로 대체되고 실행 취소 기록이
              삭제됩니다. 현재 편집 내용이 사라질 수 있습니다.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex-row border-t border-gray-100 p-0 sm:space-x-0">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setIsRegenerateConfirmOpen(false)}
              className="h-auto flex-1 rounded-none rounded-bl-2xl px-4 py-3.5 text-sm font-medium text-gray-600 hover:bg-gray-50 hover:text-gray-700"
            >
              취소
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={handleReGenerate}
              className="h-auto flex-1 rounded-none rounded-br-2xl border-l border-gray-100 px-4 py-3.5 text-sm font-medium text-red-500 hover:bg-red-50 hover:text-red-600"
            >
              재생성
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
};

export default PresentationHeader;
