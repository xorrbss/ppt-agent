/**
 * The "AI 저작 / SVG" generation-strategy radio group, plus the SVG
 * source-content-svg capability status line shown under it. Extracted from
 * UploadPage.tsx (file-size ratchet split); behaviour unchanged, only the
 * module boundary moved.
 */
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
}: GenerationStrategySelectorProps) => (
  <fieldset className="mt-4" data-testid="generation-strategy-selector">
    <legend className="mb-2 text-sm font-semibold text-[#242629]">생성 방식</legend>
    <div className="flex flex-wrap gap-2">
      {([
        ["authored", "AI 저작", "기본 방식"],
        ["svg", "SVG", sourceContentMode ? "이미지형 원본 화면을 A4로 보존" : "벡터 원본으로 생성"],
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
      <p className="mt-2 text-xs text-slate-500" role="status">SVG 원본 보존 지원을 확인하고 있습니다.</p>
    ) : sourceContentMode && sourceSvgCapability === false && pageSize !== "A4" ? (
      <p className="mt-2 text-xs text-amber-700" role="status">이 서버에서는 원본 콘텐츠 유지 SVG 생성을 아직 사용할 수 없습니다.</p>
    ) : null}
  </fieldset>
);

export default GenerationStrategySelector;
