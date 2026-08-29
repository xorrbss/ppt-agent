import { createHash } from "node:crypto";

import { validateHybridDataImageUrl } from "./security.ts";

type JsonRecord = Record<string, unknown>;

const DIGEST = /^sha256:[a-f0-9]{64}$/;
const QID = /^[A-Za-z][A-Za-z0-9._:-]{0,127}$/;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function attribute(tag: string, name: string): string | undefined {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = tag.match(
    new RegExp(`\\b${escaped}\\s*=\\s*(?:"([^"]*)"|'([^']*)'|([^\\s>]+))`, "i"),
  );
  return match ? (match[1] ?? match[2] ?? match[3] ?? "") : undefined;
}

/**
 * Replace an image's mutable source locator with the exact bytes sealed in the
 * canonical Scene. The returned HTML is self-contained before security
 * preflight and isolated Chromium; no URL or filesystem path is resolved here.
 */
export function materializeCanonicalSceneAssetsForSlide(
  presentation: unknown,
  html: string,
  slideIndex: number,
): string {
  if (!isRecord(presentation) || !isRecord(presentation.theme)) return html;
  const projection = presentation.theme.authored_vnext;
  if (!isRecord(projection) || !isRecord(projection.scene)) return html;
  const scene = projection.scene;
  if (!Array.isArray(scene.assetManifest) || !Array.isArray(scene.slides)) {
    throw new Error("authored_hybrid_scene_asset_manifest_invalid");
  }
  const sceneSlide = scene.slides[slideIndex];
  if (!isRecord(sceneSlide) || !Array.isArray(sceneSlide.elements)) {
    throw new Error("authored_hybrid_scene_slide_invalid");
  }

  const assets = new Map<string, { digest: string; dataUrl: string }>();
  for (const value of scene.assetManifest) {
    if (!isRecord(value)) {
      throw new Error("authored_hybrid_scene_asset_manifest_invalid");
    }
    const assetId = value.assetId;
    const assetDigest = value.assetDigest;
    const storageReference = value.storageReference;
    const sizeBytes = value.sizeBytes;
    if (
      typeof assetId !== "string" ||
      !assetId.startsWith("asset:") ||
      typeof assetDigest !== "string" ||
      !DIGEST.test(assetDigest) ||
      typeof storageReference !== "string" ||
      !Number.isSafeInteger(sizeBytes)
    ) {
      throw new Error("authored_hybrid_scene_asset_manifest_invalid");
    }
    const image = validateHybridDataImageUrl(storageReference);
    if (
      !image.ok ||
      image.bytes.length !== sizeBytes ||
      `sha256:${createHash("sha256").update(image.bytes).digest("hex")}` !==
        assetDigest
    ) {
      throw new Error("authored_hybrid_scene_asset_manifest_invalid");
    }
    const previous = assets.get(assetId);
    if (
      previous &&
      (previous.digest !== assetDigest || previous.dataUrl !== storageReference)
    ) {
      throw new Error("authored_hybrid_scene_asset_manifest_invalid");
    }
    assets.set(assetId, { digest: assetDigest, dataUrl: storageReference });
  }

  const sourcesByQid = new Map<string, string>();
  for (const value of sceneSlide.elements) {
    if (!isRecord(value) || value.kind !== "image") continue;
    const qid = value.elementId;
    const assetId = value.assetId;
    const assetDigest = value.assetDigest;
    if (
      typeof qid !== "string" ||
      !QID.test(qid) ||
      typeof assetId !== "string" ||
      typeof assetDigest !== "string"
    ) {
      throw new Error("authored_hybrid_scene_image_invalid");
    }
    const asset = assets.get(assetId);
    if (!asset || asset.digest !== assetDigest || sourcesByQid.has(qid)) {
      throw new Error("authored_hybrid_scene_image_invalid");
    }
    sourcesByQid.set(qid, asset.dataUrl);
  }
  if (!sourcesByQid.size) return html;

  const matched = new Set<string>();
  const materialized = html.replace(/<img\b[^>]*>/gi, (tag) => {
    const qid = attribute(tag, "data-qid");
    if (!qid || !sourcesByQid.has(qid)) return tag;
    if (matched.has(qid)) {
      throw new Error("authored_hybrid_scene_asset_dom_mismatch");
    }
    matched.add(qid);
    const source = sourcesByQid.get(qid)!;
    const sourceAttribute = /\bsrc\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/i;
    if (!sourceAttribute.test(tag)) {
      throw new Error("authored_hybrid_scene_asset_dom_mismatch");
    }
    return tag.replace(sourceAttribute, `src="${source}"`);
  });
  if (matched.size !== sourcesByQid.size) {
    throw new Error("authored_hybrid_scene_asset_dom_mismatch");
  }
  return materialized;
}
