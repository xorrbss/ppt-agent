import { isFragmentBand } from "./raster-fragments.ts";
import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import {
  authoredCanvasPhysicalSize,
  LEGACY_AUTHORED_CANVAS,
  type AuthoredCanvasContract,
} from "../authored-canvas.ts";
import type { LocalFitObservation } from "./local-fit-browser.ts";
import type { AuthoredHybridSlideLayer } from "./pptx-assembler.ts";
import type { AuthoredHybridSlideV1 } from "./schema.ts";
import type { PreparedNativeElement } from "./native-plan.ts";

const sourceObservations = new WeakMap<AuthoredHybridSlideV1, LocalFitObservation>();
export function rememberLocalFitSource(slide: AuthoredHybridSlideV1, observation?: LocalFitObservation) {
  if (observation) sourceObservations.set(slide, observation);
}
function durableObservation(observation?: LocalFitObservation) {
  if (!observation) return null;
  const { local_image_sources: _pixels, ...evidence } = observation;
  return evidence;
}
export function localFitImageEvidence(slide: AuthoredHybridSlideV1, elements: readonly PreparedNativeElement[]) {
  const observation = sourceObservations.get(slide);
  const result: { pngDigest: string; ownerQids: string[];
    placement: { x: number; y: number; widthPt: number; heightPt: number };
    sourceBoundsPx: { x: number; y: number; width: number; height: number };
    textPaint: "source" | "suppressed"; observation: ReturnType<typeof durableObservation> }[] = [];
  const visit = (element: PreparedNativeElement) => {
    if (element.kind === "group") { element.children.forEach(visit); return; }
    if (element.kind !== "image" || !observation) return;
    const source = observation.local_image_sources?.find((candidate) =>
      candidate.element_id === element.source.id && candidate.data_url === element.source.image.src);
    if (!source || element.source.image.objectFit !== "fill" ||
      Object.values(element.source.image.crop).some((value) => value !== 0)) return;
    const bounds = element.source.bounds.px;
    result.push({ pngDigest: localFitDigest(element.png),
      ownerQids: [...new Set(source.run_indices.map((index) => observation.runs[index]?.owner_qid).filter((qid): qid is string => Boolean(qid)))],
      placement: { x: bounds.x * .75, y: bounds.y * .75, widthPt: bounds.width * .75, heightPt: bounds.height * .75 },
      sourceBoundsPx: source.sourceBoundsPx, textPaint: source.text_paint,
      observation: durableObservation(observation) });
  };
  elements.forEach(visit);
  return result;
}

export interface LocalFitCaptureEvidence {
  observation?: LocalFitObservation;
  rasterObservations?: readonly { pngDigest: string; observation?: LocalFitObservation }[];
  localImages?: ReturnType<typeof localFitImageEvidence>;
}
export function localFitDigest(value: string | Buffer): string {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}
export function createLocalFitExportEvidence(
  layers: readonly AuthoredHybridSlideLayer[], htmls: readonly string[],
  sourceDigest: string, bytes: Buffer,
  canvas: AuthoredCanvasContract = LEGACY_AUTHORED_CANVAS,
) {
  const pageSize = authoredCanvasPhysicalSize(canvas);
  return {
    schemaVersion: "authored-local-fit-export-evidence/v1",
    sourceDigest, pptxDigest: localFitDigest(bytes),
    slides: layers.map((layer) => ({
      slideNumber: layer.slideNumber,
      htmlDigest: localFitDigest(htmls[layer.slideNumber - 1] ?? ""),
      observation: durableObservation(layer.localFitCapture?.observation),
      local_images: layer.localFitCapture?.localImages ?? [],
      raster: (layer.rasterBands?.length ? layer.rasterBands : [{
        rasterPng: layer.backplatePng, semanticOwnerQids: layer.semanticOwnerQids ?? [], zBand: 0,
      }]).map((band) => {
        const pngDigest = localFitDigest(band.rasterPng);
        return {
          pngDigest, ownerQids: band.semanticOwnerQids, zBand: band.zBand,
          ...(isFragmentBand(band) ? {
            schemaVersion: band.schemaVersion,
            sourceDigest: band.sourceDigest,
            captureWidthPx: band.captureWidthPx,
            captureHeightPx: band.captureHeightPx,
            fragments: band.fragments.map(fragment => ({
              id: fragment.id, order: fragment.order, rect: fragment.rect,
              pngDigest: fragment.digest, sizeBytes: fragment.sizeBytes,
              placement: {
                x: fragment.rect.x / band.captureWidthPx * pageSize.widthPt,
                y: fragment.rect.y / band.captureHeightPx * pageSize.heightPt,
                widthPt: fragment.rect.width / band.captureWidthPx * pageSize.widthPt,
                heightPt: fragment.rect.height / band.captureHeightPx * pageSize.heightPt,
              },
            })),
          } : {}),
          placement: {
            x: 0,
            y: 0,
            widthPt: pageSize.widthPt,
            heightPt: pageSize.heightPt,
          },
          observation: durableObservation(layer.localFitCapture?.rasterObservations?.find(
            (capture) => capture.pngDigest === pngDigest,
          )?.observation),
        };
      }),
    })),
  };
}
export type LocalFitExportEvidence = ReturnType<typeof createLocalFitExportEvidence>;

/** Same export ownership and atomic publication pattern as quality sidecars. */
export async function writeLocalFitExportEvidence(path: string, evidence: LocalFitExportEvidence) {
  const destination = `${path}.local-fit.json`, temporary = `${destination}.${randomUUID()}.tmp`;
  try {
    await fs.writeFile(temporary, JSON.stringify(evidence), { flag: "wx", mode: 0o600 });
    await fs.rename(temporary, destination);
  } finally {
    await fs.rm(temporary, { force: true }).catch(() => {});
  }
}
