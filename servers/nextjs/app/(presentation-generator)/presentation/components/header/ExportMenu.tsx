"use client";

import { Download, ArrowUpRight } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import type { PptxExportMode } from "./types";
import { presentationExportQualityAdvisory } from "../../utils/masterPresentation";
import { resolveAuthoredCanvas } from "@/lib/authored-canvas";

export function sourcePreservingSvgPdfUnavailable(presentation: unknown): boolean {
  const deck = presentation as {
    mode?: string;
    profile?: { driver_id?: string };
    theme?: { svg?: { sourcePreservation?: unknown } };
    slides?: Array<{ content?: { svgReceipt?: { source_preservation?: unknown } } }>;
  } | null | undefined;
  if (deck?.mode !== "svg" && deck?.profile?.driver_id !== "svg-v1") return false;
  return deck.theme?.svg?.sourcePreservation != null ||
    Boolean(deck.slides?.some((slide) => slide.content?.svgReceipt?.source_preservation != null));
}

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
  const pdfUnavailable = sourcePreservingSvgPdfUnavailable(presentation);
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
          <Button type="button" onClick={() => { closeExportOptions(); void onExportPptx("fidelity"); }} disabled={blocked} data-testid="export-pptx" variant="ghost" className="h-auto min-h-[72px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl px-3 py-3 text-left text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed" aria-label={t("fidelityAria")}>
            <span className="min-w-0 flex-1"><span className="block font-semibold leading-5">{t("fidelityLabel", { width: authoredCanvas?.widthPx ?? "", height: authoredCanvas?.heightPx ?? "" })}</span><span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-[#5F5B66]">{t("fidelityDescription", { width: authoredCanvas?.widthPx ?? "", height: authoredCanvas?.heightPx ?? "" })}</span></span>
            <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>
          {!hasMasterPin && hybridDisabledReason === undefined && <Button type="button" onClick={() => { closeExportOptions(); void onExportPptx("hybrid", true); }} disabled={exportDisabled} data-testid="authored-export-hybrid" variant="ghost" className="h-auto min-h-[88px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl border border-violet-200 bg-violet-50/60 px-3 py-3 text-left text-xs text-black hover:bg-violet-50 focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed" aria-label={t("hybridDefaultAria")}>
            <span className="min-w-0 flex-1"><span className="block font-semibold leading-5">{t("hybridDefaultLabel")}</span><span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-violet-950" role="note">{t("hybridDefaultDescription")}</span></span>
            <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>}
          {!hasMasterPin && hybridDisabledReason === undefined && <Button type="button" onClick={() => { closeExportOptions(); void onExportPptx("hybrid", false); }} disabled={exportDisabled} data-testid="authored-export-hybrid-compatibility" variant="ghost" className="h-auto min-h-[88px] w-full items-start justify-between gap-3 whitespace-normal rounded-xl border border-amber-200 bg-amber-50/60 px-3 py-3 text-left text-xs text-black hover:bg-amber-50 focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed" aria-label={t("hybridCompatibilityAria")}>
            <span className="min-w-0 flex-1"><span className="block font-semibold leading-5">{t("hybridCompatibilityLabel")}</span><span className="mt-0.5 block text-[11px] font-normal leading-[1.45] text-amber-900" role="note">{t("hybridCompatibilityDescription")}</span></span>
            <ArrowUpRight className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>}
          {isAuthoredDeck && manifestBindingLabel ? <p className="px-3 text-[11px] leading-4 text-slate-500" data-testid="authored-export-manifest-binding">{manifestBindingLabel}</p> : null}
          {isAuthoredDeck && hybridDisabledReason ? <p className="px-3 text-[11px] leading-4 text-amber-800" role="note" data-testid="authored-export-hybrid-unavailable">{t("hybridDisabled", { reason: hybridDisabledReason })}</p> : null}
          {!hasMasterPin && onOpenHybridExportHistory && <Button type="button" onClick={() => { closeExportOptions(); onOpenHybridExportHistory(); }} disabled={historyDisabled} data-testid="authored-export-hybrid-history" variant="ghost" className="h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] disabled:cursor-not-allowed">
            {t("hybridHistory")} <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          </Button>}
        </>}
        <Button onClick={() => { if (pdfUnavailable) return; closeExportOptions(); void onExportPdf(); }} disabled={blocked || pdfUnavailable} aria-describedby={pdfUnavailable ? "svg-pdf-unavailable" : undefined} data-testid="export-pdf" variant="ghost" className={`h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] ${mobile ? "border-none bg-white" : ""}`}>
          PDF <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        </Button>
        {pdfUnavailable && <p id="svg-pdf-unavailable" className="px-3 text-xs text-slate-500">{t("sourcePreservingSvgPdfUnavailable")}</p>}
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
