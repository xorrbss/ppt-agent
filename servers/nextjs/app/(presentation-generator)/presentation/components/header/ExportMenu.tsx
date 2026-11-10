"use client";

import { Download, ArrowUpRight } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

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
  exportDisabled: boolean;
  workspaceCommandsEnabled: boolean;
  onExportPptx: () => Promise<void>;
  onExportPdf: () => Promise<void>;
  /** The deck itself; a source-preserving svg deck has no PDF export. */
  presentation?: unknown;
  /** An export operation for this presentation is already running or queued. */
  exportOperationInFlight?: boolean;
}

const ExportMenu = ({
  exportDisabled,
  workspaceCommandsEnabled,
  onExportPptx,
  onExportPdf,
  presentation,
  exportOperationInFlight = false,
}: ExportMenuProps) => {
  const t = useTranslations("exportMenu");
  const [open, setOpen] = useState(false);
  const exportTriggerRef = useRef<HTMLButtonElement>(null);
  const closeExportOptions = () => {
    setOpen(false);
    requestAnimationFrame(() => exportTriggerRef.current?.focus());
  };

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
      <div className="my-[18px] h-[1px] bg-[#E8E8E8]" />
      <div className="space-y-2">
        <Button onClick={() => { if (pdfUnavailable) return; closeExportOptions(); void onExportPdf(); }} disabled={blocked || pdfUnavailable} aria-describedby={pdfUnavailable ? "svg-pdf-unavailable" : undefined} data-testid="export-pdf" variant="ghost" className={`h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] ${mobile ? "border-none bg-white" : ""}`}>
          PDF <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        </Button>
        {pdfUnavailable && <p id="svg-pdf-unavailable" className="px-3 text-xs text-slate-500">{t("sourcePreservingSvgPdfUnavailable")}</p>}
        <Button onClick={() => { closeExportOptions(); void onExportPptx(); }} disabled={blocked} data-testid="export-pptx" variant="ghost" className={`h-auto min-h-10 w-full justify-between gap-3 whitespace-normal rounded-lg px-3 py-2.5 text-xs text-black hover:bg-[#F6F6F9] focus-visible:ring-[#B8B3F5] ${mobile ? "bg-white" : ""}`}>
          PPTX <ArrowUpRight className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        </Button>
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
