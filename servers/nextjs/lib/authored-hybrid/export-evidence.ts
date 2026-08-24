import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";

import type { BundledPresentationExportResult } from "@/lib/run-bundled-presentation-export";

import type { PresentationExportQualityReport } from "../presentation-export-quality.ts";
import {
  createAuthoredHybridCoverageManifest,
  summarizeAuthoredHybridExport,
} from "./export-metadata.ts";

export interface AuthoredHybridEvidenceSource {
  exportAttemptId: string;
  presentationId: string;
  presentationRevision: number;
  canonicalSourceDigest: string;
  sourceDigest: string;
  pptxSha256: string;
}

export async function attachAuthoredHybridEvidence(
  result: BundledPresentationExportResult,
  quality: PresentationExportQualityReport,
  source: AuthoredHybridEvidenceSource,
): Promise<BundledPresentationExportResult> {
  const qualityPath = `${result.path}.quality.json`;
  const coveragePath = `${result.path}.coverage.json`;
  const suffix = randomUUID();
  const temporaryQualityPath = `${qualityPath}.${suffix}.tmp`;
  const temporaryCoveragePath = `${coveragePath}.${suffix}.tmp`;
  const boundQuality: PresentationExportQualityReport = {
    ...quality,
    exportAttemptId: source.exportAttemptId,
    atomicBinding: {
      presentationId: source.presentationId,
      presentationRevision: source.presentationRevision,
      canonicalSourceDigest: source.canonicalSourceDigest,
      rendererSourceDigest: source.sourceDigest,
      pptxSha256: source.pptxSha256,
    },
    powerPointQa: {
      status: process.platform === "win32" ? "not-verified" : "preflight",
      platform: process.platform === "win32" ? "windows" : "unsupported",
    },
  };
  const coverageManifest = createAuthoredHybridCoverageManifest(
    boundQuality,
    source,
  );
  let qualityPublished = false;
  let coveragePublished = false;
  try {
    await fs.writeFile(temporaryQualityPath, JSON.stringify(boundQuality, null, 2), {
      flag: "wx",
      mode: 0o600,
    });
    await fs.writeFile(
      temporaryCoveragePath,
      JSON.stringify(coverageManifest, null, 2),
      { flag: "wx", mode: 0o600 },
    );
    await fs.rename(temporaryQualityPath, qualityPath);
    qualityPublished = true;
    await fs.rename(temporaryCoveragePath, coveragePath);
    coveragePublished = true;
    await fs.chmod(qualityPath, 0o644);
    await fs.chmod(coveragePath, 0o644);
  } catch (error) {
    await Promise.all([
      fs.rm(temporaryQualityPath, { force: true }).catch(() => {}),
      fs.rm(temporaryCoveragePath, { force: true }).catch(() => {}),
      ...(qualityPublished ? [fs.rm(qualityPath, { force: true }).catch(() => {})] : []),
      ...(coveragePublished ? [fs.rm(coveragePath, { force: true }).catch(() => {})] : []),
      fs.rm(result.path, { force: true }).catch(() => {}),
    ]);
    throw new Error("authored_hybrid_coverage_publication_failed", { cause: error });
  }
  const exportMetadata = summarizeAuthoredHybridExport(
    boundQuality.slides.map((slide) => ({
      editable: slide.editable,
      fallbackReason: slide.fallbackReasons[0],
    })),
  );
  exportMetadata.coverageManifest = coverageManifest;
  return { ...result, quality: boundQuality, exportMetadata };
}
