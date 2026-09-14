"use client";

import { Download, ArrowUpRight } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import type { PptxExportMode } from "./types";
import { presentationExportQualityAdvisory } from "../../utils/masterPresentation";
import { resolveAuthoredCanvas } from "@/lib/authored-canvas";

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
  /** The deck itself; its quality receipt is shown as export advisory evidence. */
  presentation?: unknown;
  manifestBindingLabel?: string;
  /** An export operation for this presentation is already running or queued. */
  exportOperationInFlight?: boolean;
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
  presentation,
  manifestBindingLabel,
  exportOperationInFlight = false,
}: ExportMenuProps) => {
  const t = useTranslations("exportMenu");
  const [open, setOpen] = useState(false);
  const exportTriggerRef = useRef<HTMLButtonElement>(null);
  const closeExportOptions = () => {
    setOpen(false);
    requestAnimationFrame(() => exportTriggerRef.current?.focus());
  };

  const qualityAdvisory = isAuthoredDeck
    ? presentationExportQualityAdvisory(presentation)
    : undefined;
  const authoredCanvas = isAuthoredDeck
    ? resolveAuthoredCanvas((presentation as { theme?: unknown } | undefined)?.theme)
    : undefined;
  const blocked = exportDisabled;
  const triggerReason = exportOperationInFlight
    ? t("triggerDisabledExportInFlightAria")
    : workspaceCommandsEnabled
      ? t("triggerAria")
      : t("triggerDisabledAria");
  const ExportOptions = ({ mobile }: { mobile: boolean }) => (
    <div className={`rounded-[18px] max-md:mt-4 ${mobile ? "" : "bg-white"} p-5`} aria-label={t("formatGroup")} role="group">
      <p className="text-sm font-medium text-[#19001F]">{t("chooseFormat")}</p>
      {qualityAdvisory ? <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-[11px] leading-4 text-amber-900" role="note" data-testid="export-quality-advisory">{qualityAdvisory}</p> : null}
      <div className="my-[18px] h-[1px] bg-[#E8E8E8]" />
      <div className="space-y-2">
        {isAuthoredDeck && <>
          <Button type="button" onClick={() => { closeExportOptions(); void onExportPptx("fidelity"); }} disabled={blocked} data-testid="export-pptx" variant="ghost" className="h-auto min-h-[72px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl px-3 py-3 text-left text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed" aria-label="Fidelity PPTX 내보내기">
            <span className="min-w-0 flex-1"><span className="block font-semibold leading-5">Fidelity PPTX · {authoredCanvas?.widthPx}×{authoredCanvas?.heightPx} raster</span><span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-[#5F5B66]">각 슬라이드의 {authoredCanvas?.widthPx}×{authoredCanvas?.heightPx} 렌더를 보존합니다. 접근 가능한 텍스트·개체 편집은 제공하지 않으며, CJK 글자 크기·밀도와 overflow/occlusion은 릴리스 receipt로 별도 확인합니다.</span></span>
            <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>
          {!hasMasterPin && hybridDisabledReason === undefined && <Button type="button" onClick={() => { closeExportOptions(); void onExportPptx("hybrid", true); }} disabled={exportDisabled} data-testid="authored-export-hybrid" variant="ghost" className="h-auto min-h-[88px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl border border-violet-200 bg-violet-50/60 px-3 py-3 text-left text-xs text-black hover:bg-violet-50 focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed" aria-label="Hybrid PPTX raster fallback 내보내기, 글꼴 포함 기본값">
            <span className="min-w-0 flex-1"><span className="block font-semibold leading-5">Hybrid PPTX · raster fallback (기본)</span><span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-violet-950" role="note">Hybrid PPTX (기본) · 글꼴 포함 — 텍스트·도형은 편집 가능한 개체로, 그라디언트·그림자 등은 배경 그림으로 내보냅니다.</span></span>
            <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>}
          {!hasMasterPin && hybridDisabledReason === undefined && <Button type="button" onClick={() => { closeExportOptions(); void onExportPptx("hybrid", false); }} disabled={exportDisabled} data-testid="authored-export-hybrid-compatibility" variant="ghost" className="h-auto min-h-[88px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl border border-amber-200 bg-amber-50/60 px-3 py-3 text-left text-xs text-black hover:bg-amber-50 focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed" aria-label="Hybrid PPTX raster fallback 호환 모드, 글꼴 미포함">
            <span className="min-w-0 flex-1"><span className="block font-semibold leading-5">Hybrid PPTX · raster fallback 호환 모드</span><span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-amber-900" role="note">Hybrid PPTX · 호환 모드 · 글꼴 미포함 — 한글 텍스트가 그림으로 내보내져 PowerPoint에서 편집할 수 없습니다. 글꼴 포함 파일이 열리지 않을 때만 사용하세요.</span></span>
            <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>}
          {isAuthoredDeck && manifestBindingLabel ? <p className="px-3 text-[11px] leading-4 text-slate-500" data-testid="authored-export-manifest-binding">{manifestBindingLabel}</p> : null}
          {isAuthoredDeck && hybridDisabledReason ? <p className="px-3 text-[11px] leading-4 text-amber-800" role="note" data-testid="authored-export-hybrid-unavailable">Hybrid 비활성: {hybridDisabledReason}</p> : null}
          {!hasMasterPin && onOpenHybridExportHistory && <Button type="button" onClick={() => { closeExportOptions(); onOpenHybridExportHistory(); }} disabled={historyDisabled} data-testid="authored-export-hybrid-history" variant="ghost" className="h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed">
            Hybrid 내보내기 이력 및 증거 <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>}
        </>}
        <Button onClick={() => { closeExportOptions(); void onExportPdf(); }} disabled={blocked} data-testid="export-pdf" variant="ghost" className={`h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] ${mobile ? "border-none bg-white" : ""}`}>
          PDF <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        </Button>
        {!isAuthoredDeck && <Button onClick={() => { closeExportOptions(); void onExportPptx(); }} disabled={blocked} data-testid="export-pptx" variant="ghost" className={`h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] ${mobile ? "bg-white" : ""}`}>
          PPTX <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        </Button>}
      </div>
    </div>
  );

  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild><Button ref={exportTriggerRef} type="button" variant="toolbar" size="toolbar" disabled={exportDisabled} data-testid="export-trigger" aria-label={triggerReason} title={exportOperationInFlight ? triggerReason : undefined}>
      <Download className="h-4 w-4" aria-hidden="true" /> {t("trigger")}
    </Button></PopoverTrigger>
    <PopoverContent align="end" className="w-[min(22rem,calc(100vw-2rem))] max-h-[calc(100vh-2rem)] overflow-y-auto rounded-[18px] space-y-2 p-0"><ExportOptions mobile={false} /></PopoverContent>
  </Popover>;
};

export default ExportMenu;
