import type { ActiveSlideScene } from "@/components/authored-vnext";

import type { DeckMasterContractV1 } from "../master/contracts.ts";
import type { Bounds, DeckDocument, SlideId, WorkbenchOperation } from "../workbench/model.ts";
import type { OutlineLayoutPlan } from "./types.ts";

export interface SceneLabels {
  header: string;
  footer: string;
  logo: string;
  divider: string;
  pageNumber: string;
  title: string;
  body: string;
}

/**
 * The Workbench's local `data.set` name deliberately describes a typed patch.
 * This adapter is the only place it crosses into the canonical Scene API,
 * where the persisted mutation is named `data.update` and must carry the
 * complete deck/slide CAS fence. `sceneDigest` stays in the idempotency fence,
 * not the strict request body.
 */
export interface SceneMutationRequest {
  readonly command: {
    readonly schemaVersion: "scene-mutation-command/v1";
    readonly operationId: `operation:${string}`;
    readonly deckId: DeckDocument["id"];
    readonly slideId: SlideId;
    readonly elementId: Exclude<WorkbenchOperation, { kind: "layout.apply" }>["elementId"];
    readonly kind: "text.replace" | "geometry.update" | "asset.set" | "data.update";
    readonly payload: Record<string, unknown>;
    readonly expectedSlideRevision: number;
    readonly expectedSlideDigest: DeckDocument["slides"][number]["digest"];
    readonly publicationFence: {
      readonly expectedDeckRevision: number;
      readonly expectedActiveRevisionDigest: DeckDocument["digest"];
    };
  };
  readonly idempotencyFence: DeckDocument["sceneDigest"];
}

export class SceneMutationCapabilityError extends Error {
  constructor(code: string) {
    super(code);
    this.name = "SceneMutationCapabilityError";
  }
}

export function sceneMutationRequestForOperation(
  deckId: DeckDocument["id"],
  operationId: `operation:${string}`,
  operation: WorkbenchOperation,
): SceneMutationRequest {
  if (operation.kind === "layout.apply") {
    throw new SceneMutationCapabilityError("authored_vnext_scene_layout_mutation_unsupported");
  }
  const payload: Record<string, unknown> = operation.kind === "text.replace"
    ? { text: operation.text }
    : operation.kind === "geometry.update"
      ? { bounds: operation.bounds }
      : operation.kind === "asset.set"
        ? {
            assetId: operation.assetId,
            assetDigest: operation.digest,
            altText: operation.altText,
            crop: operation.crop,
            ...(operation.mediaType ? { mediaType: operation.mediaType } : {}),
            ...(operation.storageReference ? { storageReference: operation.storageReference } : {}),
            sizeBytes: operation.sizeBytes,
          }
        : {
            dataId: operation.dataId,
            dataDigest: operation.dataDigest,
            dataPayload: operation.dataPayload,
            ...(operation.rows === undefined ? {} : { rows: operation.rows }),
            ...(operation.columns === undefined ? {} : { columns: operation.columns }),
            ...(operation.chartType === undefined ? {} : { chartType: operation.chartType }),
            ...(operation.accessibilitySummary === undefined
              ? {}
              : { accessibilitySummary: operation.accessibilitySummary }),
          };
  return {
    command: {
      schemaVersion: "scene-mutation-command/v1",
      operationId,
      deckId,
      slideId: operation.slideId,
      elementId: operation.elementId,
      kind: operation.kind === "data.set" ? "data.update" : operation.kind,
      payload,
      expectedSlideRevision: operation.base.slideRevision,
      expectedSlideDigest: operation.base.slideDigest,
      publicationFence: {
        expectedDeckRevision: operation.base.deckRevision,
        expectedActiveRevisionDigest: operation.base.deckDigest,
      },
    },
    idempotencyFence: operation.base.sceneDigest,
  };
}

const FURNITURE_TEXT_CONTRAST_MINIMUM = 4.5;
const HEX_COLOR = /^#?([0-9a-fA-F]{6})$/;

export class FurnitureContrastCapabilityError extends Error {
  constructor(code: string) {
    super(code);
    this.name = "FurnitureContrastCapabilityError";
  }
}

function hexColor(value: unknown, fallback: string): string {
  const match = typeof value === "string" ? HEX_COLOR.exec(value) : null;
  return `#${(match?.[1] ?? fallback.replace(/^#/, "")).toUpperCase()}`;
}

function requiredHexColor(value: unknown): string {
  const match = typeof value === "string" ? HEX_COLOR.exec(value) : null;
  if (!match) throw new FurnitureContrastCapabilityError("authored_vnext_master_furniture_effective_color_invalid");
  return `#${match[1].toUpperCase()}`;
}

function relativeLuminance(color: string): number {
  const channels = color.slice(1).match(/.{2}/g);
  if (!channels || channels.length !== 3) {
    throw new FurnitureContrastCapabilityError("authored_vnext_master_furniture_color_invalid");
  }
  const linear = channels.map((channel) => {
    const value = Number.parseInt(channel, 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(foreground: string, background: string): number {
  const left = relativeLuminance(foreground);
  const right = relativeLuminance(background);
  return (Math.max(left, right) + 0.05) / (Math.min(left, right) + 0.05);
}

function furnitureTextColor(
  item: DeckMasterContractV1["furniture"][keyof DeckMasterContractV1["furniture"]],
  contract: DeckMasterContractV1,
  background: string,
) {
  const requestedColor = hexColor(item.fill ?? item.stroke, "111827");
  if (item.effectiveColor !== undefined) {
    const effectiveColor = requiredHexColor(item.effectiveColor);
    const ratio = contrastRatio(effectiveColor, background);
    if (ratio < FURNITURE_TEXT_CONTRAST_MINIMUM) {
      throw new FurnitureContrastCapabilityError("authored_vnext_master_furniture_contrast_unsupported");
    }
    return {
      requestedColor,
      effectiveColor,
      contrastRatio: ratio,
      contrastAdjusted: effectiveColor !== requestedColor,
      source: "backend" as const,
    };
  }
  const candidates = [
    requestedColor,
    contract.tokens.colors.textPrimary,
    contract.tokens.colors.textSecondary,
    contract.tokens.colors.accent,
    contract.tokens.colors.warning,
    contract.tokens.colors.background,
  ].map((candidate) => hexColor(candidate, "111827"));
  const effectiveColor = [...new Set(candidates)].find(
    (candidate) => contrastRatio(candidate, background) >= FURNITURE_TEXT_CONTRAST_MINIMUM,
  );
  if (!effectiveColor) {
    throw new FurnitureContrastCapabilityError("authored_vnext_master_furniture_contrast_unsupported");
  }
  const ratio = contrastRatio(effectiveColor, background);
  return {
    requestedColor,
    effectiveColor,
    contrastRatio: ratio,
    contrastAdjusted: effectiveColor !== requestedColor,
    source: "computed" as const,
  };
}

export function outlineForPrompt(
  prompt: string,
  count: number,
  coverPurpose: string,
  slidePurpose: (ordinal: number) => string,
): OutlineLayoutPlan {
  const subject = prompt.trim();
  return {
    planId: "plan:authored-vnext",
    revision: 1,
    slides: Array.from({ length: count }, (_, index) => ({
      slideId: `slide:${index + 1}`,
      ordinal: index + 1,
      title: index === 0 ? subject : `${subject} · ${index + 1}`,
      purpose: index === 0 ? coverPurpose : slidePurpose(index + 1),
      layoutId:
        index === 0
          ? "layout:cover"
          : index === 1 || index % 15 === 0
            ? "layout:section"
            : index % 10 === 5
              ? "layout:long-title"
              : "layout:standard",
    })),
  };
}

export async function digestText(value: string): Promise<`sha256:${string}`> {
  const bytes = new TextEncoder().encode(value.normalize("NFC"));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${[...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

function normalize(bounds: Bounds, contract: DeckMasterContractV1) {
  return {
    x: bounds.x / contract.canvas.widthPx,
    y: bounds.y / contract.canvas.heightPx,
    width: bounds.width / contract.canvas.widthPx,
    height: bounds.height / contract.canvas.heightPx,
  };
}

/** Storage references are server-sealed, but the browser only receives same-origin paths.
 * A filesystem/blob/external reference is deliberately not rendered as a successful image preview. */
function browserSafeAssetPreview(storageReference: string | undefined): string | undefined {
  if (!storageReference || !storageReference.startsWith("/") || storageReference.startsWith("//")) return undefined;
  const path = storageReference.split("?")[0] ?? "";
  return path.split("/").some((segment) => segment === "..") ? undefined : storageReference;
}

export function sceneForMaster(
  deck: DeckDocument,
  activeSlideId: SlideId | null,
  contract: DeckMasterContractV1,
  labels: SceneLabels,
): ActiveSlideScene | null {
  const slide = deck.slides.find((item) => item.id === activeSlideId) ?? null;
  if (!slide) return null;
  const slideIndex = deck.slides.indexOf(slide);
  const cover = slideIndex === 0;
  // Background is company-standard furniture, never a required body Scene
  // element. This keeps the same canonical body bytes usable with every
  // standard while the Workbench still previews the selected one accurately.
  const background = hexColor(contract.tokens.colors.background, "E5E7EB");
  const masterElements = Object.entries(contract.furniture)
    .filter(([, item]) => item.visibility === "always" || (cover ? item.visibility === "coverOnly" : item.visibility === "exceptCover"))
    .map(([role, item]) => {
      const label = labels[role as keyof SceneLabels] ?? role;
      const textColor = item.kind === "text" || item.kind === "pageNumber"
        ? furnitureTextColor(item, contract, background)
        : null;
      const common = {
        id: item.id,
        label,
        bounds: normalize(item.bounds, contract),
        style: {
          color: textColor?.effectiveColor ?? item.stroke ?? contract.tokens.colors.textSecondary,
          backgroundColor: item.fill,
          borderColor: item.stroke,
          fontFamily: contract.fontManifest.primary,
        },
        ...(textColor ? { furnitureTextColor: textColor } : {}),
      };
      if (item.kind === "logo") return { ...common, kind: "logo" as const };
      if (item.kind === "pageNumber") {
        return { ...common, kind: "page-number" as const, text: String(slideIndex + 1).padStart(2, "0") };
      }
      if (item.kind === "line") return { ...common, kind: "line" as const };
      if (item.kind === "rect") return { ...common, kind: "shape" as const };
      return { ...common, kind: "text" as const, text: item.text ?? "" };
    });
  const slideTitle = slide.nodes.find((node) => node.kind === "text" && node.role === "title");
  const bodyElements = [...slide.nodes].sort((left, right) => left.zOrder - right.zOrder).map((node) => {
    if (node.kind === "text") {
      return {
        id: node.id,
        kind: "text" as const,
        label: node.semanticRole === "title" ? labels.title : node.semanticRole,
        semanticRole: node.semanticRole,
        editability: node.editability,
        text: node.text,
        bounds: { ...normalize(node.bounds, contract), rotation: node.rotation },
        style: {
          color: contract.tokens.colors.textPrimary,
          fontFamily: contract.fontManifest.primary,
          fontSize: node.role === "title"
            ? contract.semanticStyles.title.fontPt
            : contract.semanticStyles.body.fontPt,
        },
      };
    }
    if (node.kind === "image" || node.kind === "asset") {
      const previewUrl = browserSafeAssetPreview(node.storageReference);
      return {
        id: node.id,
        kind: "asset" as const,
        label: node.altText,
        semanticRole: node.semanticRole,
        editability: node.editability,
        assetId: node.assetId,
        assetDigest: node.digest,
        alt: node.altText,
        crop: node.crop,
        fit: node.fit,
        ...(node.mediaType ? { mediaType: node.mediaType } : {}),
        ...(node.storageReference ? { storageReference: node.storageReference } : {}),
        ...(node.sizeBytes ? { sizeBytes: node.sizeBytes } : {}),
        ...(previewUrl ? { previewUrl } : { previewUnavailableReason: "authored_vnext_asset_preview_reference_unavailable" }),
        bounds: { ...normalize(node.bounds, contract), rotation: node.rotation },
      };
    }
    if (node.kind === "shape") {
      return {
        id: node.id,
        kind: "shape" as const,
        label: node.semanticRole,
        semanticRole: node.semanticRole,
        shape: node.shape,
        editability: node.editability,
        bounds: { ...normalize(node.bounds, contract), rotation: node.rotation },
        style: {
          backgroundColor: node.fill ?? contract.tokens.colors.accent,
          borderColor: node.stroke ?? contract.tokens.colors.textSecondary,
        },
      };
    }
    if (node.kind === "table" || node.kind === "chart") {
      return {
        id: node.id,
        kind: node.kind,
        label: node.semanticRole,
        semanticRole: node.semanticRole,
        editability: node.editability,
        bounds: { ...normalize(node.bounds, contract), rotation: node.rotation },
        dataId: node.dataId,
        dataDigest: node.dataDigest,
        ...(node.dataPayload ? { dataPayload: { ...node.dataPayload } } : {}),
        ...(node.kind === "table" ? { rows: node.rows, columns: node.columns } : { chartType: node.chartType, accessibilitySummary: node.accessibilitySummary }),
        capabilityMessage: `Scene ${node.kind} preview is data-pinned; only sealed data and geometry mutations are available.`,
      };
    }
    return {
      id: node.id,
      kind: node.kind,
      label: node.semanticRole,
      semanticRole: node.semanticRole,
      editability: node.editability,
      bounds: { ...normalize(node.bounds, contract), rotation: node.rotation },
      capabilityMessage: `Scene ${node.kind} editing is limited to the sealed capabilities for this object.`,
    };
  });
  return {
    slideId: slide.id,
    revision: slide.revision,
    title: slideTitle?.kind === "text" ? slideTitle.text : slide.id,
    backgroundColor: background,
    masterRevisionLabel: `master r${deck.master.revision}`,
    masterElements,
    bodyElements,
  };
}

export function slideLabel(slide: DeckDocument["slides"][number]): string {
  const title = slide.nodes.find((node) => node.kind === "text" && node.role === "title");
  return title?.kind === "text" ? title.text : slide.id;
}
