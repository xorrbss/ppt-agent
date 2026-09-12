'use client'
import React, { useEffect, useRef } from "react";

import { Card } from "@/components/ui/card";
import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import {
  AlertTriangle,
  EllipsisVertical,
  Loader2,
  Share2,
  Trash,
} from "lucide-react";
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from "@/components/ui/popover";
import { usePathname, useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { notify } from "@/components/ui/sonner";

import { applyPresentationThemeToElement } from "@/app/(presentation-generator)/presentation/utils/applyPresentationThemeDom";
import { DeferredSlideThumbnail } from "./DeferredSlideThumbnail";
import MarkdownRenderer from "@/components/MarkDownRender";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";
import { SharePopover } from "./SharePopover";
import { getPresentationDisplayTitle } from "@/lib/presentation-display";
import { tryResolveServerIssuedPresentationProfile } from "@/lib/presentation-profile";
import { formatViewerDate } from "@/i18n/viewer-time";

export const PresentationCard = ({
  id,
  title: storedTitle,
  presentation,
  onDeleted
}: {
  id: string;
  title: string;
  presentation: any;
  onDeleted?: (presentationId: string) => void;
}) => {
  const t = useTranslations("community");
  const tWorkspace = useTranslations("slideWorkspace");
  const title = getPresentationDisplayTitle({ ...presentation, title: storedTitle }, tWorkspace("untitledPresentation"));
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const [showDeleteDialog, setShowDeleteDialog] = React.useState(false);
  const [showShareDialog, setShowShareDialog] = React.useState(false);
  const [isDeleting, setIsDeleting] = React.useState(false);
  const presentationRuntime = tryResolveServerIssuedPresentationProfile(presentation);
  const presentationDriverId = presentationRuntime?.profile.driver_id ?? null;

  const deleteDialogCancelRef = useRef<HTMLButtonElement>(null);
  const deleteDialogRef = useRef<HTMLDivElement>(null);
  const menuTriggerRef = useRef<HTMLButtonElement>(null);
  const previouslyFocusedRef = useRef<HTMLElement | null>(null);
  const isDeletingRef = useRef(false);

  const handlePreview = () => {
    if (showDeleteDialog || showShareDialog) return;
    if (!presentationDriverId) {
      notify.error(t("unsupportedProfile"));
      return;
    }
    trackEvent(MixpanelEvent.Dashboard_Presentation_Opened, {
      pathname,
      presentation_id: id,
      title_length: (title || "").length,
      slide_count: presentation?.slides?.length || 0,
      presentation_driver_id: presentationDriverId,
    });
    router.push(`/presentation?id=${id}&driver=${presentationDriverId}`);
  };
  useEffect(() => {
    // Unified theme application keeps thumbnails aligned with editor/export
    // rendering while preserving the retained theme token wire shape.
    const element = document.getElementById(`dashboard-presentation-card-${id}`)
    applyPresentationThemeToElement(element, presentation.theme)
  }, [id, presentation.theme])

  useEffect(() => {
    isDeletingRef.current = isDeleting;
  }, [isDeleting]);

  useEffect(() => {
    if (!showDeleteDialog) return;

    previouslyFocusedRef.current = document.activeElement as HTMLElement | null;
    const menuTrigger = menuTriggerRef.current;
    deleteDialogCancelRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isDeletingRef.current) {
        event.preventDefault();
        setShowDeleteDialog(false);
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = deleteDialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable?.length) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      const returnTarget = previouslyFocusedRef.current?.isConnected
        ? previouslyFocusedRef.current
        : menuTrigger;
      returnTarget?.focus?.();
    };
  }, [showDeleteDialog]);

  const handleDelete = async () => {
    if (isDeleting) return;
    setIsDeleting(true);
    const response = await DashboardApi.deletePresentation(id);

    if (response?.success) {
      trackEvent(MixpanelEvent.Dashboard_Presentation_Deleted, {
        pathname,
        presentation_id: id,
        slide_count: presentation?.slides?.length || 0,
      });
      notify.success("발표자료 삭제됨", "발표자료가 대시보드에서 삭제되었습니다.");
      setShowDeleteDialog(false);
      if (onDeleted) {
        onDeleted(id);
      }
    } else {
      notify.error("발표자료를 삭제할 수 없습니다", response?.message || "발표자료를 삭제하는 중 문제가 발생했습니다.");
    }
    setIsDeleting(false);
  };
  const firstSlide = presentation?.slides?.[0];
  return (
    <Card
      suppressHydrationWarning={true}
      onClick={handlePreview}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          handlePreview();
        }
      }}
      role="link"
      tabIndex={0}
      aria-label={`${title || "발표자료"} 열기`}
      className="bg-zinc-50 font-syne shadow-none presentation-card rounded-2xl border border-zinc-200 p-0 group hover:border-zinc-300 hover:shadow-[0_8px_24px_-12px_rgba(24,24,27,0.2)] transition-all duration-200 slide-theme cursor-pointer overflow-hidden flex flex-col focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:ring-offset-2"
    >
      <div
        id={`dashboard-presentation-card-${id}`}
        suppressHydrationWarning={true} className="flex flex-col flex-1 relative z-40">
        {/* <p className=" text-xs font-syne absolute top-2 flex gap-1 capitalize  items-center left-2 rounded-[100px]  px-2.5 py-1 bg-[#3A3A3AF5] text-white font-semibold  z-40 ">

          {presentation.type}
        </p> */}

        
        <div className="scale-[0.85] my-3 border border-zinc-200 rounded-md overflow-hidden shadow-sm">

          <DeferredSlideThumbnail slide={firstSlide} />
        </div>

        <div className="w-full px-4 py-4 mt-auto z-40 relative bg-white border-t border-zinc-200/70">
          <div className="flex items-center justify-between gap-3 w-full">
            <div className="flex min-w-0 flex-col items-start gap-1.5">
              <div className="text-sm text-[#191919] font-semibold  overflow-hidden line-clamp-1">
                <MarkdownRenderer content={title} className="text-sm mb-0  font-syne text-[#191919] font-semibold  overflow-hidden line-clamp-1" />
              </div>
              <p className="text-zinc-400 text-xs font-syne">
                {formatViewerDate(presentation?.created_at, locale)}
              </p>

            </div>
            <Popover>
              <PopoverTrigger
                ref={menuTriggerRef}
                aria-label={`${title || "발표자료"} 메뉴`}
                className="flex h-8 w-8 items-center justify-center rounded-full text-gray-500 hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5146E5]"
                onClick={(event) => event.stopPropagation()}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.preventDefault();
                  event.stopPropagation();
                  event.currentTarget.click();
                }}
              >
                <EllipsisVertical className="h-5 w-5 text-gray-500" />
              </PopoverTrigger>
              <PopoverContent align="end" className="w-[200px] rounded-xl border-zinc-200 bg-white p-1.5 shadow-lg">
                <button
                  type="button"
                  className="flex w-full items-center justify-between px-2 py-1 hover:bg-gray-100"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setShowShareDialog(true);
                  }}
                >
                  <p>공유</p>
                  <Share2 className="h-4 w-4 text-[#5146E5]" />
                </button>
                <button
                  type="button"
                  className="flex items-center justify-between w-full px-2 py-1 hover:bg-gray-100"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setShowDeleteDialog(true);
                  }}
                >
                  <p>삭제</p>
                  <Trash className="w-4 h-4 text-red-500" />
                </button>
              </PopoverContent>
            </Popover>
          </div>

        </div>
      </div>
      {showDeleteDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center animate-[fadeIn_150ms_ease-out]"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            if (isDeleting) return;
            setShowDeleteDialog(false);
          }}
        >
          <div className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" />
          <div
            ref={deleteDialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={`presentation-delete-title-${id}`}
            aria-describedby={`presentation-delete-description-${id}`}
            className="relative w-[360px] rounded-2xl bg-white shadow-2xl animate-[scaleIn_200ms_ease-out]"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
          >
            <div className="flex flex-col items-center p-6 pb-4 text-center">
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-50">
                <AlertTriangle className="h-6 w-6 text-red-500" />
              </div>
              <h3 id={`presentation-delete-title-${id}`} className="mb-2 text-lg font-semibold text-[#191919]">
                발표자료를 삭제하시겠습니까?
              </h3>
              <p id={`presentation-delete-description-${id}`} className="text-sm leading-relaxed text-gray-500">
                <span className="font-medium text-gray-700">&quot;{title}&quot;</span>
                {" "}발표자료를 삭제하려고 합니다. 이 작업은 되돌릴 수 없습니다.
              </p>
            </div>
            <div className="flex border-t border-gray-100">
              <button
                ref={deleteDialogCancelRef}
                type="button"
                onClick={() => setShowDeleteDialog(false)}
                disabled={isDeleting}
                className="flex-1 px-4 py-3.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                취소
              </button>
              <button
                type="button"
                onClick={() => void handleDelete()}
                disabled={isDeleting}
                className="flex flex-1 items-center justify-center gap-2 border-l border-gray-100 px-4 py-3.5 text-sm font-medium text-red-500 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isDeleting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    삭제 중…
                  </>
                ) : (
                  "삭제"
                )}
              </button>
            </div>
          </div>
        </div>
      )}
      <SharePopover
        presentationId={id}
        title={title}
        open={showShareDialog}
        onOpenChange={setShowShareDialog}
      />
    </Card>
  );
};
