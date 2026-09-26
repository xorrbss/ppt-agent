import { createHash } from "node:crypto";
import { authoredCanvasPhysicalSize, LEGACY_AUTHORED_CANVAS, type AuthoredCanvasContract } from "../authored-canvas.ts";
import type { PreparedNativeElement } from "./native-plan.ts";
import type { AuthoredHybridSlideLayer } from "./pptx-assembler.ts";

/**
 * Authored slides commonly start with an opaque, full-canvas rectangle that
 * supplies only the page colour. It is editable geometry, but it must remain
 * below the residual raster; otherwise it hides raster-only illustrations
 * while later text and small shapes still appear correctly.
 */
export function isCanvasBackgroundShape(item: PreparedNativeElement, canvas: AuthoredCanvasContract = LEGACY_AUTHORED_CANVAS): boolean {
  if (item.kind !== "shape") return false;
  const { x, y, width, height } = item.source.bounds.px;
  const shape = item.source.shape;
  const fillIsOpaque = shape.fill
    ? shape.fill.alpha * item.source.opacity >= 0.995
    : Boolean(
        shape.gradient?.stops.length &&
          shape.gradient.stops.every(
            (stop) => stop.color.alpha * item.source.opacity >= 0.995
          )
      );
  return (
    shape.shape === "rectangle" &&
    fillIsOpaque &&
    Math.abs(item.source.rotationDeg) < 0.001 &&
    x <= 0.5 &&
    y <= 0.5 &&
    x + width >= canvas.widthPx - 0.5 &&
    y + height >= canvas.heightPx - 0.5
  );
}

export const SEMANTIC_OWNERSHIP_ALT_TEXT_PREFIX =
  "presenton-semantic-raster-ownership-v1:";

export function semanticOwnerQids(layer: AuthoredHybridSlideLayer): string[] {
  // A band list attributes a raster owner to the exact transparent plane that
  // paints it, so no two bands may claim the same QID. It is however only a
  // subset of the slide's raster owners: an owner whose paint lands on a
  // native band -- a structural container whose native object was dropped
  // because its group could not be closed -- belongs to no raster island and
  // would silently lose its carrier. The layer's own list is the authority for
  // how many carriers this slide owes, and readback compares against exactly
  // that set, so band attribution is extended with the remaining owners.
  const banded = layer.rasterBands
    ? layer.rasterBands.flatMap((band) => [...band.semanticOwnerQids])
    : [];
  const declared = [...(layer.semanticOwnerQids ?? [])];
  if (banded.length + declared.length > 2048) {
    throw new Error("Hybrid semantic ownership QID count exceeds the bounded limit.");
  }
  const unique = new Set<string>();
  const ordered: string[] = [];
  const admit = (qid: unknown, rejectRepeat: boolean): void => {
    if (typeof qid !== "string" || !/^[A-Za-z][A-Za-z0-9._:-]{0,95}$/.test(qid)) {
      throw new Error("Hybrid semantic ownership QIDs must be unique bounded strings.");
    }
    if (unique.has(qid)) {
      if (rejectRepeat) {
        throw new Error("Hybrid semantic ownership QIDs must be unique bounded strings.");
      }
      return;
    }
    unique.add(qid);
    ordered.push(qid);
  };
  for (const qid of banded) admit(qid, true);
  // Without bands the declared list is the only source and keeps its own
  // uniqueness contract; with bands it may legitimately repeat what a band
  // already attributed.
  for (const qid of declared) admit(qid, banded.length === 0);
  return ordered;
}

export function semanticOwnershipAltText(slideNumber: number, qids: readonly string[]): string {
  const qidSetDigest = `sha256:${createHash("sha256")
    .update(qids.join("\n"), "utf8")
    .digest("hex")}`;
  const payload = Buffer.from(JSON.stringify({
    schemaVersion: "presenton-semantic-raster-ownership/v1",
    slideNumber,
    qids,
    qidSetDigest,
  }), "utf8").toString("base64url");
  return `${SEMANTIC_OWNERSHIP_ALT_TEXT_PREFIX}${payload}`;
}

export function backplatePictureXml(
  relationshipId: string,
  _slideNumber: number,
  _qids: readonly string[],
  nonVisualId = 2,
  bandIndex = 0,
  canvas: AuthoredCanvasContract = LEGACY_AUTHORED_CANVAS,
  placement?: { x: number; y: number; width: number; height: number; captureWidth: number; captureHeight: number },
): string {
  // Semantic QIDs live only on the hidden exact-owner carriers. Repeating the
  // set on a raster picture would create a second logical owner in readback.
  const description = "";
  const name = bandIndex === 0
    ? "DevAX hybrid backplate"
    : `DevAX hybrid raster z-band ${bandIndex}`;
  const size = authoredCanvasPhysicalSize(canvas);
  const x = placement ? Math.round(placement.x / placement.captureWidth * size.widthEmu) : 0;
  const y = placement ? Math.round(placement.y / placement.captureHeight * size.heightEmu) : 0;
  const width = placement ? Math.round((placement.x+placement.width) / placement.captureWidth * size.widthEmu)-x : size.widthEmu;
  const height = placement ? Math.round((placement.y+placement.height) / placement.captureHeight * size.heightEmu)-y : size.heightEmu;
  return `<p:pic><p:nvPicPr><p:cNvPr id="${nonVisualId}" name="${name}"${description}/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="${relationshipId}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="${x}" y="${y}"/><a:ext cx="${width}" cy="${height}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr></p:pic>`;
}

export function semanticOwnershipShapeXml(
  nonVisualId: number,
  slideNumber: number,
  qid: string,
): string {
  const name = escapeXmlAttribute(
    `DevAX raster visual fragment ${qid} semantic-owner`,
  );
  const description = escapeXmlAttribute(
    semanticOwnershipAltText(slideNumber, [qid]),
  );
  return `<p:sp><p:nvSpPr><p:cNvPr id="${nonVisualId}" name="${name}" descr="${description}" hidden="1"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1" cy="1"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr></p:sp>`;
}

export function backplateBackgroundXml(relationshipId: string): string {
  return `<p:bg><p:bgPr><a:blipFill dpi="0" rotWithShape="1"><a:blip r:embed="${relationshipId}"/><a:stretch><a:fillRect/></a:stretch></a:blipFill><a:effectLst/></p:bgPr></p:bg>`;
}

export function isOpaqueRgbPng(png: Buffer): boolean {
  // The PNG IHDR colour-type byte is 2 for true-colour RGB without an alpha
  // channel. Such a residual covers the entire canvas (typically a page-level
  // gradient), so keeping it as a selectable picture only creates a misleading
  // full-slide object. A real slide background preserves the appearance while
  // leaving all extracted text, boxes, lines, and icons independently editable.
  return png.length > 25 && png[25] === 2;
}


export function escapeXmlAttribute(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
}

