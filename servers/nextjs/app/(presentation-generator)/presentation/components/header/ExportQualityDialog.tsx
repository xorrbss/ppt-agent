"use client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { PresentationExportQualityReport } from "@/lib/presentation-export-quality";

const ExportQualityDialog = ({
  exportQuality,
  setExportQuality,
}: {
  exportQuality: PresentationExportQualityReport | null;
  setExportQuality: (report: PresentationExportQualityReport | null) => void;
}) => (
  <Dialog
    open={exportQuality !== null}
    onOpenChange={(nextOpen) => {
      if (!nextOpen) setExportQuality(null);
    }}
  >
    <DialogContent
      className="w-[420px] rounded-2xl sm:max-w-[420px]"
      data-testid="export-quality-dialog"
    >
      <DialogHeader>
        <DialogTitle>PPTX 편집 품질</DialogTitle>
        <DialogDescription>
          전체 {exportQuality?.totalSlides ?? 0}장 · 편집 요소 포함{" "}
          {exportQuality?.editableSlides ?? 0}장 · 이미지 fallback{" "}
          {exportQuality?.imageFallbackSlides ?? 0}장
        </DialogDescription>
      </DialogHeader>
      {exportQuality &&
        (exportQuality.status !== "fully-editable" ||
          exportQuality.imageFallbackSlides > 0 ||
          exportQuality.rasterFallbackElements > 0) && (
        <div
          className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900"
          role="alert"
        >
          일부 콘텐츠는 이미지로 남아 완전 편집형이 아닙니다.
        </div>
      )}
      {exportQuality && (
        <div className="space-y-2 text-sm text-gray-600">
          <p>
            네이티브 텍스트 {exportQuality.nativeTextElements}개 · 도형{" "}
            {exportQuality.nativeShapeElements}개 · 그룹{" "}
            {exportQuality.nativeGroupElements ?? 0}개 · 이미지{" "}
            {exportQuality.nativeImageElements}개 · 잔여 raster{" "}
            {exportQuality.rasterFallbackElements}개
          </p>
          {exportQuality.slides.some(
            (slide) => slide.fallbackReasons.length > 0
          ) && (
            <div className="max-h-36 overflow-y-auto rounded-lg bg-gray-50 p-3">
              {exportQuality.slides
                .filter((slide) => slide.fallbackReasons.length > 0)
                .flatMap((slide) =>
                  (slide.fallbackElements?.length ?? 0) > 0
                    ? (slide.fallbackElements ?? []).map((element) => (
                        <p
                          key={`${slide.slideNumber}:${element.elementId}`}
                        >
                          {slide.slideNumber}장 · {element.elementId} (
                          {element.candidateKind}):{" "}
                          {element.reasons.join(", ")}
                        </p>
                      ))
                    : [
                        <p key={slide.slideNumber}>
                          {slide.slideNumber}장:{" "}
                          {slide.fallbackReasons.join(", ")}
                        </p>,
                      ]
                )}
            </div>
          )}
          <p className="text-xs text-gray-500">
            {exportQuality.fontEmbeddingStatus?.applied
              ? `OOXML 폰트 임베딩 ${exportQuality.fontEmbeddingStatus.embeddedFontFiles}개가 적용되었습니다.`
              : exportQuality.fontEmbeddingStatus?.requested
                ? "글꼴 포함을 요청했지만 적용되지 않아 호환 typeface/fallback을 사용했습니다."
                : "이 내보내기에는 글꼴이 포함되지 않아 호환 typeface/fallback을 사용합니다."}
          </p>
        </div>
      )}
      <DialogFooter>
        <Button type="button" onClick={() => setExportQuality(null)}>
          확인
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
);

export default ExportQualityDialog;
