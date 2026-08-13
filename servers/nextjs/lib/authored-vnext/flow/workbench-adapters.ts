import type { ActiveSlideScene } from "@/components/authored-vnext";

import type { DeckMasterContractV1 } from "../master/contracts.ts";
import type { Bounds, DeckDocument, SlideId } from "../workbench/model.ts";
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
  const masterElements = Object.entries(contract.furniture)
    .filter(([, item]) => item.visibility === "always" || (cover ? item.visibility === "coverOnly" : item.visibility === "exceptCover"))
    .map(([role, item]) => {
      const label = labels[role as keyof SceneLabels] ?? role;
      const common = {
        id: item.id,
        label,
        bounds: normalize(item.bounds, contract),
        style: {
          color: item.stroke ?? contract.tokens.colors.textSecondary,
          backgroundColor: item.fill,
          borderColor: item.stroke,
          fontFamily: contract.fontManifest.primary,
        },
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
  return {
    slideId: slide.id,
    revision: slide.revision,
    title: slideTitle?.kind === "text" ? slideTitle.text : slide.id,
    masterRevisionLabel: `master r${deck.master.revision}`,
    masterElements,
    bodyElements: slide.nodes.map((node) => node.kind === "text"
      ? {
          id: node.id,
          kind: "text" as const,
          label: node.role === "title" ? labels.title : labels.body,
          text: node.text,
          bounds: normalize(node.bounds, contract),
          style: {
            color: contract.tokens.colors.textPrimary,
            fontFamily: contract.fontManifest.primary,
            fontSize: node.role === "title"
              ? contract.semanticStyles.title.fontPt
              : contract.semanticStyles.body.fontPt,
          },
        }
      : {
          id: node.id,
          kind: "asset" as const,
          label: node.altText,
          assetId: node.assetId,
          alt: node.altText,
          fit: "contain" as const,
          bounds: normalize(node.bounds, contract),
        }),
  };
}

export function slideLabel(slide: DeckDocument["slides"][number]): string {
  const title = slide.nodes.find((node) => node.kind === "text" && node.role === "title");
  return title?.kind === "text" ? title.text : slide.id;
}
