'use client'
import React, { useEffect } from "react";

import { Card } from "@/components/ui/card";
import { DashboardApi } from "@/app/(presentation-generator)/services/api/dashboard";
import { AlertTriangle, EllipsisVertical, Loader2, Trash } from "lucide-react";
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from "@/components/ui/popover";
import { usePathname, useRouter } from "next/navigation";
import { notify } from "@/components/ui/sonner";

import { applyPresentationThemeToElement } from "@/app/(presentation-generator)/presentation/utils/applyPresentationThemeDom";
import SlideScale from "@/app/(presentation-generator)/components/PresentationRender";
import MarkdownRenderer from "@/components/MarkDownRender";
import { trackEvent, MixpanelEvent } from "@/utils/mixpanel";

export const PresentationCard = ({
  id,
  title,
  presentation,
  onDeleted
}: {
  id: string;
  title: string;
  presentation: any;
  onDeleted?: (presentationId: string) => void;
}) => {
  const router = useRouter();
  const pathname = usePathname();
  const [showDeleteDialog, setShowDeleteDialog] = React.useState(false);
  const [isDeleting, setIsDeleting] = React.useState(false);

  const handlePreview = (e: React.MouseEvent) => {
    e.preventDefault();
    trackEvent(MixpanelEvent.Dashboard_Presentation_Opened, {
      pathname,
      presentation_id: id,
      title_length: (title || "").length,
      slide_count: presentation?.slides?.length || 0,
    });
    router.push(`/presentation?id=${id}&type=standard`);
  };
  useEffect(() => {
    applyTheme(presentation.theme)
  }, [])
  const applyTheme = async (theme: any) => {
    // Unified theme application (base 16 vars + fonts + adaptive tone & manner
    // tokens) so adaptive-deck thumbnails match the editor/export render. Legacy
    // thumbnails are unaffected — base vars are identical, extended tokens are
    // consumed only by the adaptive renderer.
    const element = document.getElementById(`dashboard-presentation-card-${id}`)
    applyPresentationThemeToElement(element, theme)
  }

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
      className="bg-[#F8FBFB] font-syne shadow-none sm:shadow-none  presentation-card rounded-[12px] p-0 group hover:shadow-md transition-all duration-500 slide-theme cursor-pointer overflow-hidden flex flex-col"
    >
      <div
        id={`dashboard-presentation-card-${id}`}
        suppressHydrationWarning={true} className="flex flex-col flex-1 relative z-40">
        {/* <p className=" text-xs font-syne absolute top-2 flex gap-1 capitalize  items-center left-2 rounded-[100px]  px-2.5 py-1 bg-[#3A3A3AF5] text-white font-semibold  z-40 ">

          {presentation.type}
        </p> */}

        <img src="/card_bg.svg" alt="" className="absolute top-0 left-0 w-full h-full object-cover" />
        <div className="scale-[0.75] mt-4  border border-gray-300 rounded-lg overflow-hidden">

          <SlideScale slide={firstSlide} isClickable={false} />
        </div>

        <div className="w-full py-3 px-5 mt-auto z-40 relative bg-white  border-t border-[#EDEEEF]">
          <div className="flex items-center justify-between gap-7 w-full">
            <div className="flex flex-col items-start gap-1">
              <div className="text-sm text-[#191919] font-semibold  overflow-hidden line-clamp-1">
                <MarkdownRenderer content={title} className="text-sm mb-0  font-syne text-[#191919] font-semibold  overflow-hidden line-clamp-1" />
              </div>
              <p className="text-[#808080] text-sm font-syne">
                {new Date(presentation?.created_at).toLocaleDateString()}
              </p>

            </div>
            <Popover>
              <PopoverTrigger className="w-6 h-6 hover:bg-gray-100 rounded-full flex items-center justify-center text-gray-500 hover:text-gray-700" onClick={(e) => e.stopPropagation()}>
                <EllipsisVertical className="w-6 h-6 text-gray-500" />
              </PopoverTrigger>
              <PopoverContent align="end" className="bg-white w-[200px]">
                <button
                  className="flex items-center justify-between w-full px-2 py-1 hover:bg-gray-100"
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    setShowDeleteDialog(true);
                  }}
                >
                  <p>삭제</p>
                  <Trash className="w- h-4 text-red-500" />
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
              <h3 className="mb-2 text-lg font-semibold text-[#191919]">
                발표자료를 삭제하시겠습니까?
              </h3>
              <p className="text-sm leading-relaxed text-gray-500">
                <span className="font-medium text-gray-700">&quot;{title}&quot;</span>
                {" "}발표자료를 삭제하려고 합니다. 이 작업은 되돌릴 수 없습니다.
              </p>
            </div>
            <div className="flex border-t border-gray-100">
              <button
                onClick={() => setShowDeleteDialog(false)}
                disabled={isDeleting}
                className="flex-1 px-4 py-3.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                취소
              </button>
              <button
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
    </Card>
  );
};
