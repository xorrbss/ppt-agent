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

export type HybridCoverageManifest = {
  schemaVersion: "presenton.authored-hybrid-coverage/v1";
  mode: "hybrid";
  source: { presentationRevision: number; sourceDigest: string };
  totalSlides: number;
  nativeText: number;
  nativeImage: number;
  nativeShape: number;
  nativeTable: number;
  nativeChart: number;
  rasterRemaining: number;
  fullyEditable: boolean;
  slides: Array<{
    slideNumber: number;
    nativeText: number;
    nativeImage: number;
    nativeShape: number;
    nativeTable: number;
    nativeChart: number;
    rasterRemaining: number;
    fallbackReasons: string[];
    visualParity: "fidelity-backplate-retained" | "not-verified";
    nativeBackplateDeDuplication: "applied" | "not-applicable";
  }>;
};

const HybridCoverageDialog = ({
  manifest,
  onClose,
}: {
  manifest: HybridCoverageManifest | null;
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
            className={`rounded-lg p-3 ${manifest.fullyEditable && manifest.rasterRemaining === 0 ? "bg-emerald-50 text-emerald-950" : "bg-amber-50 text-amber-950"}`}
            role="status"
          >
            {manifest.fullyEditable && manifest.rasterRemaining === 0
              ? "이 export의 선언된 요소는 native입니다."
              : `raster 잔여 ${manifest.rasterRemaining}개가 있어 완전 편집 가능 파일이 아닙니다.`}
          </div>
          <p>
            native 텍스트 {manifest.nativeText} · 이미지 {manifest.nativeImage}{" "}
            · 도형 {manifest.nativeShape} · 표 {manifest.nativeTable} · 차트{" "}
            {manifest.nativeChart} · raster {manifest.rasterRemaining}
          </p>
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
                  {slide.visualParity === "fidelity-backplate-retained"
                    ? "fidelity backplate 유지"
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
