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

export type HybridCoverageManifest = {
  schemaVersion: string;
  mode: "hybrid";
  source: {
    presentationRevision: number;
    sourceDigest: string;
    canonicalSourceDigest?: string;
    exportAttemptId?: string;
  };
  totalSlides: number;
  nativeText: number;
  nativeImage: number;
  nativeShape: number;
  nativeTable: number;
  nativeChart: number;
  rasterRemaining: number;
  fullyEditable: boolean;
  visualParity?: "preflight" | "not-verified" | "passed" | "failed";
  slides: Array<{
    slideNumber: number;
    nativeText: number;
    nativeImage: number;
    nativeShape: number;
    nativeTable: number;
    nativeChart: number;
    rasterRemaining: number;
    fallbackReasons: string[];
    visualParity:
      | "fidelity-backplate-retained"
      | "preflight"
      | "not-verified"
      | "passed"
      | "failed";
    nativeBackplateDeDuplication: "applied" | "not-applicable";
  }>;
};

const HybridCoverageDialog = ({
  manifest,
  exportQuality,
  onClose,
}: {
  manifest: HybridCoverageManifest | null;
  exportQuality?: PresentationExportQualityReport | null;
  onClose: () => void;
}) => (
  <Dialog open={manifest !== null} onOpenChange={(open) => !open && onClose()}>
    <DialogContent
      className="max-h-[min(680px,calc(100vh-2rem))] overflow-y-auto sm:max-w-2xl"
      data-testid="hybrid-coverage-dialog"
    >
      <DialogHeader>
        <DialogTitle>Hybrid 편집 범위</DialogTitle>
        <DialogDescription>
          revision {manifest?.source.presentationRevision ?? "-"} · digest{" "}
          {manifest?.source.sourceDigest ?? "-"}
        </DialogDescription>
      </DialogHeader>
      {manifest ? (
        <div className="space-y-3 text-sm text-slate-700">
          <div
            className="rounded-lg bg-amber-50 p-3 text-amber-950"
            role="status"
          >
            {manifest.rasterRemaining === 0
              ? "raster 잔여는 보고되지 않았습니다. 그러나 완전 편집 가능 또는 시각 동일성은 이 결과만으로 주장하지 않습니다."
              : `raster 잔여 ${manifest.rasterRemaining}개가 있어 완전 편집 가능 파일이 아닙니다.`}
          </div>
          <div className="rounded-lg border border-slate-200 p-3 text-xs text-slate-700">
            <p>export attempt: {manifest.source.exportAttemptId ?? "미기록"}</p>
            <p className="mt-1">visualParity: {manifest.visualParity ?? "not-verified"}</p>
            <p className="mt-1 break-all">renderer digest: {manifest.source.sourceDigest}</p>
            <p className="mt-1 break-all">canonical digest: {manifest.source.canonicalSourceDigest ?? "미기록"}</p>
          </div>
          <p>
            native 텍스트 {manifest.nativeText} · 이미지 {manifest.nativeImage}{" "}
            · 도형 {manifest.nativeShape} · 표 {manifest.nativeTable} · 차트{" "}
            {manifest.nativeChart} · raster {manifest.rasterRemaining}
          </p>
          {exportQuality ? (
            <div className="rounded-lg border border-slate-200 p-3 text-xs text-slate-700">
              <p className="font-medium text-slate-900">범위 및 readback 근거</p>
              <p className="mt-1">논리적 coverage: 텍스트 {exportQuality.logicalCoverage.nativeText} · 이미지 {exportQuality.logicalCoverage.nativeImage} · 도형 {exportQuality.logicalCoverage.nativeShape} · 그룹 {exportQuality.logicalCoverage.nativeGroup} · raster {exportQuality.logicalCoverage.rasterRemaining}</p>
              <p className="mt-1">OOXML readback ({exportQuality.ooxmlReadback.status}): 텍스트 {exportQuality.ooxmlReadback.nativeText} · 이미지 {exportQuality.ooxmlReadback.nativeImage} · 도형 {exportQuality.ooxmlReadback.nativeShape} · 그룹 {exportQuality.ooxmlReadback.nativeGroup} · raster {exportQuality.ooxmlReadback.rasterRemaining}</p>
              <p className="mt-1">backplate {exportQuality.backplateCount}개. native paint 제거 구성은 시각 동일성 검증이 아닙니다.</p>
              <p className="mt-1">PowerPoint QA: {exportQuality.powerPointQa ? `${exportQuality.powerPointQa.platform} · ${exportQuality.powerPointQa.status}` : "미기록"}</p>
              <p className="mt-1 break-all">PPTX digest: {exportQuality.atomicBinding?.pptxSha256 ?? "미기록"}</p>
              <p className="font-medium text-slate-900">글꼴 및 typography</p>
              <p className="mt-1">
                요청: {exportQuality.fontEmbeddingStatus?.requested ? "예" : "아니오"} · 적용: {exportQuality.fontEmbeddingStatus?.applied ? "예" : "아니오"} · 사유: {exportQuality.fontEmbeddingStatus?.reason ?? "미기록"}
              </p>
              <p className="mt-1">
                browser 글꼴 수집 {exportQuality.fontRendering?.browserFontFilesCollected ?? 0}개 · 수집 실패 {exportQuality.fontRendering?.browserCollectionFailures ?? 0}개 · typeface 정책 {exportQuality.fontRendering?.powerpointTypefacePolicy ?? "미기록"}
              </p>
              <p className="mt-1">
                글꼴 파트 {exportQuality.fontEmbeddingStatus?.embeddedFontFiles ?? 0}개 · native 텍스트 {exportQuality.fontEmbeddingStatus?.nativeTextElements ?? exportQuality.nativeTextElements}개 · 포함 글꼴 사용 native 텍스트 {exportQuality.fontEmbeddingStatus?.nativeTextElementsUsingEmbeddedTypefaces ?? "미검증"}개
              </p>
              <p className="mt-1">
                실사용 의미: {exportQuality.fontEmbeddingStatus?.nativeTextUsage === "used"
                  ? "포함된 글꼴을 실제 native 텍스트가 사용"
                  : exportQuality.fontEmbeddingStatus?.nativeTextUsage === "raster-only-unused"
                    ? "글꼴 파트는 포함됐지만 native 텍스트가 0개라 미사용 (텍스트는 raster 유지)"
                    : exportQuality.fontEmbeddingStatus?.nativeTextUsage === "not-used"
                      ? "글꼴 파트는 포함됐지만 native 텍스트가 해당 글꼴을 사용하지 않음"
                      : exportQuality.fontEmbeddingStatus?.nativeTextUsage === "not-embedded"
                        ? "글꼴 파트 미포함"
                        : "native 텍스트의 포함 글꼴 실사용 미검증"}
              </p>
              {(exportQuality.fontEmbeddingStatus?.nativeTypefaceFamilies?.length ?? 0) > 0 ? (
                <p className="mt-1">native 사용 글꼴: {exportQuality.fontEmbeddingStatus?.nativeTypefaceFamilies?.join(", ")}</p>
              ) : null}
              {(exportQuality.fontEmbeddingStatus?.failures?.length ?? 0) > 0 ? (
                <p className="mt-1 text-amber-800">
                  글꼴 fallback 사유: {exportQuality.fontEmbeddingStatus?.failures?.map((failure) => `${failure.family ?? "unknown"}: ${failure.reason}`).join(", ")}
                </p>
              ) : null}
              {exportQuality.typographyIssues.length > 0 ? (
                <p className="mt-1 text-amber-800">
                  typography fallback 사유: {exportQuality.typographyIssues.map((issue) => `${issue.elementId ?? "unknown"}: ${issue.code}`).join(", ")}
                </p>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-amber-800">글꼴 적용 상태와 typography 증거가 응답에 없어 미검증입니다.</p>
          )}
          <div className="overflow-hidden rounded-lg border border-slate-200">
            {manifest.slides.map((slide) => (
              <div
                key={slide.slideNumber}
                className="border-b border-slate-100 p-3 last:border-b-0"
              >
                <p className="font-medium">
                  {slide.slideNumber}장 · native 텍스트 {slide.nativeText},
                  이미지 {slide.nativeImage}, 도형 {slide.nativeShape}, 표{" "}
                  {slide.nativeTable}, 차트 {slide.nativeChart} · raster{" "}
                  {slide.rasterRemaining}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  시각 기준:{" "}
                  {slide.visualParity === "passed"
                    ? "동일 attempt·revision·digest·PPTX의 PowerPoint COM 검증 통과"
                    : slide.visualParity === "failed"
                      ? "PowerPoint 시각 검증 실패"
                      : slide.visualParity === "preflight"
                        ? "사전 점검만 완료 (PowerPoint 미검증)"
                        : slide.visualParity === "fidelity-backplate-retained"
                          ? "backplate 구성 유지 (시각 동일성은 별도 검증 필요)"
                          : "시각 동일성 미검증"}{" "}
                  · backplate 중복 방지:{" "}
                  {slide.nativeBackplateDeDuplication === "applied"
                    ? "적용"
                    : "해당 없음"}
                </p>
                {slide.fallbackReasons.length > 0 ? (
                  <p className="mt-1 text-xs text-amber-800">
                    fallback: {slide.fallbackReasons.join(", ")}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <DialogFooter>
        <Button type="button" onClick={onClose}>
          확인
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
);

export default HybridCoverageDialog;
