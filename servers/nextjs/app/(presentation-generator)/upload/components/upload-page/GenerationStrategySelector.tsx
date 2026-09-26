/**
 * The "AI 저작 / SVG" generation-strategy radio group, plus the SVG
 * source-content-svg capability status line shown under it. Extracted from
 * UploadPage.tsx (file-size ratchet split); behaviour unchanged, only the
 * module boundary moved.
 */
import { useTranslations } from "next-intl";
import type { PresentationGenerationStrategy } from "@/store/slices/presentationGenUpload";
import type { PresentationConfig } from "../../type";

interface GenerationStrategySelectorProps {
  sourceContentMode: boolean;
  generationStrategy: PresentationGenerationStrategy;
  onChangeStrategy: (strategy: PresentationGenerationStrategy) => void;
  sourceSvgCapability: boolean | null;
  pageSize: PresentationConfig["pageSize"];
}

export const GenerationStrategySelector = ({
  sourceContentMode,
  generationStrategy,
  onChangeStrategy,
  sourceSvgCapability,
  pageSize,
}: GenerationStrategySelectorProps) => {
  const t = useTranslations("uploadExtras");
  return (
  <fieldset className="mt-4" data-testid="generation-strategy-selector">
    <legend className="mb-2 text-sm font-semibold text-[#242629]">{t("generationMode")}</legend>
    <div className="flex flex-wrap gap-2">
      {([
        ["authored", t("aiAuthored"), t("defaultMode")],
        ["svg", "SVG", sourceContentMode ? t("preserveImageSourceOnA4") : t("generateFromVectorSource")],
      ] as const).map(([strategy, label, description]) => (
        <label key={strategy} className="flex cursor-pointer items-center gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm">
          <input
            type="radio"
            name="generation-strategy"
            value={strategy}
            checked={generationStrategy === strategy}
            onChange={() => onChangeStrategy(strategy)}
            disabled={sourceContentMode && strategy === "svg" && sourceSvgCapability !== true && pageSize !== "A4"}
            data-testid={`generation-strategy-${strategy}`}
          />
          <span><span className="font-medium">{label}</span><span className="ml-1 text-xs text-slate-500">{description}</span></span>
        </label>
      ))}
    </div>
    {sourceContentMode && sourceSvgCapability === null && pageSize !== "A4" ? (
      <p className="mt-2 text-xs text-slate-500" role="status">{t("checkingSvgSourcePreservation")}</p>
    ) : sourceContentMode && sourceSvgCapability === false && pageSize !== "A4" ? (
      <p className="mt-2 text-xs text-amber-700" role="status">{t("svgSourcePreservationUnavailable")}</p>
    ) : null}
  </fieldset>
  );
};

export default GenerationStrategySelector;
