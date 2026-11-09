/**
 * Read-only summary of the "preserve original content" selection: which
 * generation path will run, and
 * (once analysis has run) how much of the source content it found. Extracted
 * from UploadPage.tsx (file-size ratchet split); behaviour unchanged, only
 * the module boundary moved.
 */
import { useTranslations } from "next-intl";
import type { SourceContentAnalysisSummary } from "../../../services/api/source-content/contract";

interface SourceContentSummaryPanelProps {
  authoredStyle: string;
  isSourceContentAnalyzing: boolean;
  sourceContentSummary: SourceContentAnalysisSummary | null;
}

export const SourceContentSummaryPanel = ({
  authoredStyle,
  isSourceContentAnalyzing,
  sourceContentSummary,
}: SourceContentSummaryPanelProps) => {
  const t = useTranslations("uploadExtras");
  return (
  <div className="mt-4 rounded-xl border border-[#E4E6E9] bg-[#FAFAFB] px-3 py-3 text-sm text-[#33363A]" data-testid="source-content-selection-summary">
    <p className="font-semibold">{t("sourceContentPreserved")}</p>
    <p className="mt-1 text-[#5E626A]">{t("svgSelectedTemplate", { style: authoredStyle })}</p>
    <p className="mt-1 text-[#5E626A]">{t("imageInputReconstructionDescription")}</p>
    <p className="mt-1 text-[#5E626A]">{t("preserveSourcePageOrder")}</p>
    {isSourceContentAnalyzing ? <p className="mt-2 text-xs text-[#737780]">{t("analyzingSourceContentSafely")}</p> : null}
    {sourceContentSummary ? <p className="mt-2 text-xs text-[#737780]" data-testid="source-content-analysis-summary">
      {t("analysisSummary", { sourceType: sourceContentSummary.sourceType.toUpperCase(), sourceUnits: sourceContentSummary.sourceUnits, textBlocks: sourceContentSummary.textBlocks, tables: sourceContentSummary.tables, charts: sourceContentSummary.charts, uniqueImages: sourceContentSummary.uniqueImages, imageOccurrences: sourceContentSummary.imageOccurrences, rasterFallbackBlocks: sourceContentSummary.rasterFallbackBlocks })}
    </p> : null}
  </div>
  );
};

export default SourceContentSummaryPanel;
