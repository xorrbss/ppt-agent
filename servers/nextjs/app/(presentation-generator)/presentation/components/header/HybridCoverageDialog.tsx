"use client";

import { Button } from "@/components/ui/button";
import { useTranslations } from "next-intl";
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
  cjkCoverageProfileId?: string;
  nativeTextPolicy?: {
    koreanLatin: "bounded-native";
    japanese: "raster-required";
    chinese: "raster-required";
  };
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
}) => {
  const t = useTranslations("presentationHybridCoverage");
  return (
  <Dialog open={manifest !== null} onOpenChange={(open) => !open && onClose()}>
    <DialogContent
      className="max-h-[min(680px,calc(100vh-2rem))] overflow-y-auto sm:max-w-2xl"
      data-testid="hybrid-coverage-dialog"
    >
      <DialogHeader>
        <DialogTitle>{t("title")}</DialogTitle>
        <DialogDescription>
          {t("revisionDigest", { revision: manifest?.source.presentationRevision ?? "-", digest: manifest?.source.sourceDigest ?? "-" })}
        </DialogDescription>
      </DialogHeader>
      {manifest ? (
        <div className="space-y-3 text-sm text-slate-700">
          <div
            className="rounded-lg bg-amber-50 p-3 text-amber-950"
            role="status"
          >
            {manifest.rasterRemaining === 0
              ? t("noRasterRemaining")
              : t("rasterRemainingWarning", { count: manifest.rasterRemaining })}
          </div>
          <div className="rounded-lg border border-slate-200 p-3 text-xs text-slate-700">
            <p>{t("exportAttempt", { attempt: manifest.source.exportAttemptId ?? t("notRecorded") })}</p>
            <p className="mt-1">{t("cjkCoverage", { profile: manifest.cjkCoverageProfileId ?? t("notRecorded") })}</p>
            <p className="mt-1 text-amber-800">
              {t("cjkPolicy")}
            </p>
            <p className="mt-1">{t("visualParity", { status: manifest.visualParity ?? "not-verified" })}</p>
            <p className="mt-1 break-all">{t("rendererDigest", { digest: manifest.source.sourceDigest })}</p>
            <p className="mt-1 break-all">{t("canonicalDigest", { digest: manifest.source.canonicalSourceDigest ?? t("notRecorded") })}</p>
          </div>
          <p>
            {t("nativeCounts", { text: manifest.nativeText, image: manifest.nativeImage, shape: manifest.nativeShape, table: manifest.nativeTable, chart: manifest.nativeChart, raster: manifest.rasterRemaining })}
          </p>
          {exportQuality ? (
            <div className="rounded-lg border border-slate-200 p-3 text-xs text-slate-700">
              <p className="font-medium text-slate-900">{t("coverageHeading")}</p>
              <p className="mt-1">{t("logicalCoverage", { text: exportQuality.logicalCoverage.nativeText, image: exportQuality.logicalCoverage.nativeImage, shape: exportQuality.logicalCoverage.nativeShape, group: exportQuality.logicalCoverage.nativeGroup, raster: exportQuality.logicalCoverage.rasterRemaining })}</p>
              <p className="mt-1">{t("ooxmlReadback", { status: exportQuality.ooxmlReadback.status, text: exportQuality.ooxmlReadback.nativeText, image: exportQuality.ooxmlReadback.nativeImage, shape: exportQuality.ooxmlReadback.nativeShape, group: exportQuality.ooxmlReadback.nativeGroup, raster: exportQuality.ooxmlReadback.rasterRemaining })}</p>
              <p className="mt-1">{t("backplateWarning", { count: exportQuality.backplateCount })}</p>
              <p className="mt-1">{t("powerPointQa", { status: exportQuality.powerPointQa ? `${exportQuality.powerPointQa.platform} · ${exportQuality.powerPointQa.status}` : t("notRecorded") })}</p>
              <p className="mt-1 break-all">{t("pptxDigest", { digest: exportQuality.atomicBinding?.pptxSha256 ?? t("notRecorded") })}</p>
              <p className="font-medium text-slate-900">{t("fontsHeading")}</p>
              <p className="mt-1">
                {t("fontEmbeddingStatus", { requested: exportQuality.fontEmbeddingStatus?.requested ? t("yes") : t("no"), applied: exportQuality.fontEmbeddingStatus?.applied ? t("yes") : t("no"), reason: exportQuality.fontEmbeddingStatus?.reason ?? t("notRecorded") })}
              </p>
              <p className="mt-1">
                {t("browserFonts", { collected: exportQuality.fontRendering?.browserFontFilesCollected ?? 0, failed: exportQuality.fontRendering?.browserCollectionFailures ?? 0, policy: exportQuality.fontRendering?.powerpointTypefacePolicy ?? t("notRecorded") })}
              </p>
              <p className="mt-1">
                {t("fontParts", { parts: exportQuality.fontEmbeddingStatus?.embeddedFontFiles ?? 0, nativeText: exportQuality.fontEmbeddingStatus?.nativeTextElements ?? exportQuality.nativeTextElements, embeddedText: exportQuality.fontEmbeddingStatus?.nativeTextElementsUsingEmbeddedTypefaces ?? t("unverified") })}
              </p>
              <p className="mt-1">
                {t("usageMeaning", { meaning: exportQuality.fontEmbeddingStatus?.nativeTextUsage === "used"
                  ? t("usageUsed")
                  : exportQuality.fontEmbeddingStatus?.nativeTextUsage === "raster-only-unused"
                    ? t("usageRasterOnly")
                    : exportQuality.fontEmbeddingStatus?.nativeTextUsage === "not-used"
                      ? t("usageNotUsed")
                      : exportQuality.fontEmbeddingStatus?.nativeTextUsage === "not-embedded"
                        ? t("usageNotEmbedded")
                        : t("usageUnverified") })}
              </p>
              {(exportQuality.fontEmbeddingStatus?.nativeTypefaceFamilies?.length ?? 0) > 0 ? (
                <p className="mt-1">{t("nativeFonts", { families: exportQuality.fontEmbeddingStatus?.nativeTypefaceFamilies?.join(", ") ?? "" })}</p>
              ) : null}
              {(exportQuality.fontEmbeddingStatus?.failures?.length ?? 0) > 0 ? (
                <p className="mt-1 text-amber-800">
                  {t("fontFallbackReasons", { reasons: exportQuality.fontEmbeddingStatus?.failures?.map((failure) => `${failure.family ?? "unknown"}: ${failure.reason}`).join(", ") ?? "" })}
                </p>
              ) : null}
              {exportQuality.typographyIssues.length > 0 ? (
                <p className="mt-1 text-amber-800">
                  {t("typographyFallbackReasons", { reasons: exportQuality.typographyIssues.map((issue) => `${issue.elementId ?? "unknown"}: ${issue.code}`).join(", ") })}
                </p>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-amber-800">{t("fontEvidenceMissing")}</p>
          )}
          <div className="overflow-hidden rounded-lg border border-slate-200">
            {manifest.slides.map((slide) => (
              <div
                key={slide.slideNumber}
                className="border-b border-slate-100 p-3 last:border-b-0"
              >
                <p className="font-medium">
                  {t("slideCounts", { number: slide.slideNumber, text: slide.nativeText, image: slide.nativeImage, shape: slide.nativeShape, table: slide.nativeTable, chart: slide.nativeChart, raster: slide.rasterRemaining })}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {t("visualCriterion", { criterion: slide.visualParity === "passed"
                    ? t("parityPassed")
                    : slide.visualParity === "failed"
                      ? t("parityFailed")
                      : slide.visualParity === "preflight"
                        ? t("parityPreflight")
                        : slide.visualParity === "fidelity-backplate-retained"
                          ? t("parityBackplate")
                          : t("parityUnverified") })}{" "}
                  {t("backplateDeduplication", { status: slide.nativeBackplateDeDuplication === "applied" ? t("applied") : t("notApplicable") })}
                </p>
                {slide.fallbackReasons.length > 0 ? (
                  <p className="mt-1 text-xs text-amber-800">
                    {t("fallback", { reasons: slide.fallbackReasons.join(", ") })}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <DialogFooter>
        <Button type="button" onClick={onClose}>
          {t("confirm")}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
  );
};

export default HybridCoverageDialog;
