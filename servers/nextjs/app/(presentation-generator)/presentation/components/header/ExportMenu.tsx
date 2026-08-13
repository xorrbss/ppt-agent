"use client";
import { ArrowRightFromLine, ArrowUpRight } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";

import type { PptxExportMode } from "./types";

const ExportMenu = ({
  isAuthoredDeck,
  exportDisabled,
  workspaceCommandsEnabled,
  onExportPptx,
  onExportPdf,
}: {
  isAuthoredDeck: boolean;
  exportDisabled: boolean;
  workspaceCommandsEnabled: boolean;
  onExportPptx: (
    pptxMode?: PptxExportMode,
    fontEmbedding?: boolean
  ) => Promise<void>;
  onExportPdf: () => Promise<void>;
}) => {
  const [open, setOpen] = useState(false);
  const exportTriggerRef = useRef<HTMLButtonElement>(null);

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
      <div className="space-y-2">
        {isAuthoredDeck && (
          <>
            <Button
              type="button"
              onClick={() => {
                closeExportOptions();
                void onExportPptx();
              }}
              disabled={exportDisabled}
              data-testid="export-pptx"
              variant="ghost"
              className="h-auto min-h-[72px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl px-3 py-3 text-left text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed"
              aria-label="PPTX로 빠르게 내보내기"
            >
              <span className="min-w-0 flex-1">
                <span className="block font-semibold leading-5">
                  PPTX · 빠른 내보내기
                </span>
                <span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-[#5F5B66]">
                  슬라이드 모양을 그대로 유지하는 기본 내보내기입니다.
                </span>
              </span>
              <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            </Button>
            <Button
              type="button"
              onClick={() => {
                closeExportOptions();
                void onExportPptx("hybrid");
              }}
              disabled={exportDisabled}
              data-testid="authored-export-hybrid"
              variant="ghost"
              className="h-auto min-h-[72px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl px-3 py-3 text-left text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed"
              aria-label="편집 가능한 PPTX로 내보내기"
            >
              <span className="min-w-0 flex-1">
                <span className="block font-semibold leading-5">PPTX · 편집 가능</span>
                <span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-[#5F5B66]">
                  배경과 요소를 가능한 범위에서 분리하며 처리 시간이 더 걸릴 수 있습니다.
                </span>
              </span>
              <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            </Button>
            <Button
              type="button"
              onClick={() => {
                closeExportOptions();
                void onExportPptx("hybrid", true);
              }}
              disabled={exportDisabled}
              data-testid="authored-export-hybrid-embedded"
              variant="ghost"
              className="h-auto min-h-[88px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl border border-amber-200 bg-amber-50/60 px-3 py-3 text-left text-xs text-black hover:bg-amber-50 focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed"
              aria-label="PPTX로 내보내기, 편집 가능 및 글꼴 포함"
            >
              <span className="min-w-0 flex-1">
                <span className="block font-semibold leading-5">
                  PPTX · 편집 가능 + 글꼴 포함
                </span>
                <span
                  className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-amber-900"
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
            void onExportPdf();
          }}
          disabled={exportDisabled}
          data-testid="export-pdf"
          variant="ghost"
          className={`h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] ${
            mobile ? "border-none bg-white" : ""
          }`}
        >
          PDF
          <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        </Button>
        {!isAuthoredDeck && <Button
          onClick={() => {
            closeExportOptions();
            void onExportPptx();
          }}
          disabled={exportDisabled}
          data-testid="export-pptx"
          variant="ghost"
          className={`h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] ${
            mobile ? "bg-white" : ""
          }`}
        >
          PPTX
          <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        </Button>}
      </div>
    </div>
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          ref={exportTriggerRef}
          type="button"
          className="flex h-[38px] items-center gap-[7px] rounded-full px-[18px] text-sm font-semibold text-[#101323] disabled:opacity-50"
          style={{
            background:
              "linear-gradient(270deg, #D5CAFC 2.4%, #E3D2EB 27.88%, #F4DCD3 69.23%, #FDE4C2 100%)",
          }}
          disabled={exportDisabled}
          data-testid="export-trigger"
          aria-label={
            workspaceCommandsEnabled
              ? "채팅에서 내보내기 형식 선택"
              : "채팅 작업 연결 대기 중"
          }
        >
          내보내기{" "}
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
  );
};

export default ExportMenu;
