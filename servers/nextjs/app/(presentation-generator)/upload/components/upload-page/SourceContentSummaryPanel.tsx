/**
 * Read-only summary of the "preserve original content" selection: which
 * generation path will run, whether a company-standard master applies, and
 * (once analysis has run) how much of the source content it found. Extracted
 * from UploadPage.tsx (file-size ratchet split); behaviour unchanged, only
 * the module boundary moved.
 */
import type { SourceContentAnalysisSummary } from "../../../services/api/source-content/contract";
import type {
  CompanyStandardSelection,
  PresentationGenerationStrategy,
} from "@/store/slices/presentationGenUpload";

interface SourceContentSummaryPanelProps {
  generationStrategy: PresentationGenerationStrategy;
  authoredStyle: string;
  companyStandard: CompanyStandardSelection | null;
  isSourceContentAnalyzing: boolean;
  sourceContentSummary: SourceContentAnalysisSummary | null;
}

export const SourceContentSummaryPanel = ({
  generationStrategy,
  authoredStyle,
  companyStandard,
  isSourceContentAnalyzing,
  sourceContentSummary,
}: SourceContentSummaryPanelProps) => (
  <div className="mt-4 rounded-xl border border-[#E4E6E9] bg-[#FAFAFB] px-3 py-3 text-sm text-[#33363A]" data-testid="source-content-selection-summary">
    <p className="font-semibold">콘텐츠 · 원본 유지</p>
    <p className="mt-1 text-[#5E626A]">{generationStrategy === "svg" ? `생성 방식 · SVG · 선택 템플릿 ${authoredStyle}` : `AI 저작 스타일 · ${authoredStyle}`}</p>
    {generationStrategy === "authored" ? <p className="mt-1 text-[#5E626A]">회사 표준 · {companyStandard ? `${companyStandard.name} v${companyStandard.revision}` : "사용 안 함"}</p> : null}
    {generationStrategy === "svg" ? <p className="mt-1 text-[#5E626A]">이미지형 입력은 원본 화면을 편집 가능한 텍스트와 표로 A4에 재구성합니다. 화면 캡처만 이미지로 남습니다. 서버가 확신하지 못한 소수 항목만 생성 전에 확인을 요청할 수 있습니다.</p> : null}
    <p className="mt-1 text-[#5E626A]">원본 페이지 순서와 페이지 수를 유지합니다.</p>
    {isSourceContentAnalyzing ? <p className="mt-2 text-xs text-[#737780]">원본 콘텐츠를 안전하게 분석하는 중입니다.</p> : null}
    {sourceContentSummary ? <p className="mt-2 text-xs text-[#737780]" data-testid="source-content-analysis-summary">
      {sourceContentSummary.sourceType.toUpperCase()} {sourceContentSummary.sourceUnits}페이지 · 텍스트 {sourceContentSummary.textBlocks}개 · 표 {sourceContentSummary.tables}개 · 제외된 차트 {sourceContentSummary.charts}개 · 이미지 {sourceContentSummary.uniqueImages}종(표시 {sourceContentSummary.imageOccurrences}회) · 이미지로 보관 {sourceContentSummary.rasterFallbackBlocks}개
    </p> : null}
  </div>
);

export default SourceContentSummaryPanel;
