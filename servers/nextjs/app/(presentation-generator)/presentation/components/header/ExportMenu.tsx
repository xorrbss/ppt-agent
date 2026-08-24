"use client";

import { ArrowRightFromLine, ArrowUpRight } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import type { PptxExportMode } from "./types";

interface ExportMenuProps {
  isAuthoredDeck: boolean;
  hasMasterPin?: boolean;
  exportDisabled: boolean;
  workspaceCommandsEnabled: boolean;
  onExportPptx: (pptxMode?: PptxExportMode, fontEmbedding?: boolean) => Promise<void>;
  onExportPdf: () => Promise<void>;
  onOpenHybridExportHistory?: () => void;
  historyDisabled?: boolean;
  hybridDisabledReason?: string;
  manifestBindingLabel?: string;
}

const ExportMenu = ({
  isAuthoredDeck,
  hasMasterPin = false,
  exportDisabled,
  workspaceCommandsEnabled,
  onExportPptx,
  onExportPdf,
  onOpenHybridExportHistory,
  historyDisabled = false,
  hybridDisabledReason,
  manifestBindingLabel,
}: ExportMenuProps) => {
  const t = useTranslations("exportMenu");
  const [open, setOpen] = useState(false);
  const exportTriggerRef = useRef<HTMLButtonElement>(null);
  const closeExportOptions = () => {
    setOpen(false);
    requestAnimationFrame(() => exportTriggerRef.current?.focus());
  };

  const ExportOptions = ({ mobile }: { mobile: boolean }) => (
    <div className={`rounded-[18px] max-md:mt-4 ${mobile ? "" : "bg-white"} p-5`} aria-label={t("formatGroup")} role="group">
      <p className="text-sm font-medium text-[#19001F]">{t("chooseFormat")}</p>
      <div className="my-[18px] h-[1px] bg-[#E8E8E8]" />
      <div className="space-y-2">
        {isAuthoredDeck && <>
          <Button type="button" onClick={() => { closeExportOptions(); void onExportPptx("fidelity"); }} disabled={exportDisabled} data-testid="export-pptx" variant="ghost" className="h-auto min-h-[72px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl px-3 py-3 text-left text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed" aria-label="Fidelity PPTX 내보내기">
            <span className="min-w-0 flex-1"><span className="block font-semibold leading-5">Fidelity PPTX · 시각 고정</span><span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-[#5F5B66]">각 슬라이드를 격리 캡처해 시각 품질을 보존하며, native 편집 요소는 제공하지 않습니다.</span></span>
            <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>
          {!hasMasterPin && <Button type="button" onClick={() => { closeExportOptions(); void onExportPptx("hybrid", true); }} disabled={exportDisabled || hybridDisabledReason !== undefined} data-testid="authored-export-hybrid" variant="ghost" className="h-auto min-h-[88px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl border border-violet-200 bg-violet-50/60 px-3 py-3 text-left text-xs text-black hover:bg-violet-50 focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed" aria-label="Hybrid PPTX raster fallback 내보내기, 글꼴 포함 기본값">
            <span className="min-w-0 flex-1"><span className="block font-semibold leading-5">Hybrid PPTX · raster fallback (기본)</span><span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-violet-950" role="note">이 bounded v3 경로는 native subset을 지원하지 않습니다. 각 슬라이드를 raster fallback으로 보존하고 글꼴 포함 결과만 검증합니다.</span></span>
            <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>}
          {!hasMasterPin && <Button type="button" onClick={() => { closeExportOptions(); void onExportPptx("hybrid", false); }} disabled={exportDisabled || hybridDisabledReason !== undefined} data-testid="authored-export-hybrid-compatibility" variant="ghost" className="h-auto min-h-[88px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl border border-amber-200 bg-amber-50/60 px-3 py-3 text-left text-xs text-black hover:bg-amber-50 focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed" aria-label="Hybrid PPTX raster fallback 호환 모드, 글꼴 미포함">
            <span className="min-w-0 flex-1"><span className="block font-semibold leading-5">Hybrid PPTX · raster fallback 호환 모드</span><span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-amber-900" role="note">native subset은 지원하지 않으며 글꼴 미포함 호환 모드입니다. PowerPoint에서 typeface 대체가 발생할 수 있습니다.</span></span>
            <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>}
          {isAuthoredDeck && manifestBindingLabel ? <p className="px-3 text-[11px] leading-4 text-slate-500" data-testid="authored-export-manifest-binding">{manifestBindingLabel}</p> : null}
          {isAuthoredDeck && hybridDisabledReason ? <p className="px-3 text-[11px] leading-4 text-amber-800" role="note" data-testid="authored-export-hybrid-unavailable">Hybrid 비활성: {hybridDisabledReason}</p> : null}
          {!hasMasterPin && onOpenHybridExportHistory && <Button type="button" onClick={() => { closeExportOptions(); onOpenHybridExportHistory(); }} disabled={historyDisabled} data-testid="authored-export-hybrid-history" variant="ghost" className="h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed">
            Hybrid 내보내기 이력 및 증거 <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>}
        </>}
        <Button onClick={() => { closeExportOptions(); void onExportPdf(); }} disabled={exportDisabled} data-testid="export-pdf" variant="ghost" className={`h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] ${mobile ? "border-none bg-white" : ""}`}>
          PDF <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        </Button>
        {!isAuthoredDeck && <Button onClick={() => { closeExportOptions(); void onExportPptx(); }} disabled={exportDisabled} data-testid="export-pptx" variant="ghost" className={`h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] ${mobile ? "bg-white" : ""}`}>
          PPTX <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        </Button>}
      </div>
    </div>
  );

  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild><button ref={exportTriggerRef} type="button" className="flex h-[38px] items-center gap-[7px] rounded-full px-[18px] text-sm font-semibold text-[#101323] disabled:opacity-50" style={{ background: "linear-gradient(270deg, #D5CAFC 2.4%, #E3D2EB 27.88%, #F4DCD3 69.23%, #FDE4C2 100%)" }} disabled={exportDisabled} data-testid="export-trigger" aria-label={workspaceCommandsEnabled ? t("triggerAria") : t("triggerDisabledAria")}>
      {t("trigger")} <ArrowRightFromLine className="h-3.5 w-3.5" />
    </button></PopoverTrigger>
    <PopoverContent align="end" className="w-[min(22rem,calc(100vw-2rem))] max-h-[calc(100vh-2rem)] overflow-y-auto rounded-[18px] space-y-2 p-0"><ExportOptions mobile={false} /></PopoverContent>
  </Popover>;
};

export default ExportMenu;
