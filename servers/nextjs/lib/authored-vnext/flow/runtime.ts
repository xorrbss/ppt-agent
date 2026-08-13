import type { MasterDigest } from "../master/contracts.ts";
import type { DeckDocument, SlideId } from "../workbench/model.ts";
import { AuthoredVnextGenerationClient } from "./generation-client.ts";

export const generationClient = new AuthoredVnextGenerationClient();
export const ZERO_DIGEST = `sha256:${"0".repeat(64)}` as MasterDigest;

export function serverOperationId(
  operationId: string,
  suffix = "",
): `operation:${string}` {
  return `operation:${operationId.replace(/^op:/u, "")}${suffix}`;
}

export function publicationPin(deck: DeckDocument, slideId: SlideId) {
  const slide = deck.slides.find((item) => item.id === slideId);
  if (!slide) throw new Error("authored_vnext_slide_not_found");
  return {
    deckRevision: deck.revision,
    deckDigest: deck.digest,
    slideRevision: slide.revision,
    masterDigest: deck.master.digest,
  };
}
