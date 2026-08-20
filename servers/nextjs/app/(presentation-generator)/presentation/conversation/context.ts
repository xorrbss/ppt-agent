import type { ConversationAsset, ConversationContext } from "./types";

export function buildConversationContext(input: {
  viewportWidth?: number;
  viewportHeight?: number;
  zoom?: number;
  currentSlideId?: string;
  currentSlideIndex?: number;
  visibleSlideIds?: string[];
  assets?: ConversationAsset[];
}): ConversationContext {
  const hasViewport = Number.isFinite(input.viewportWidth)
    && (input.viewportWidth ?? 0) > 0
    && Number.isFinite(input.viewportHeight)
    && (input.viewportHeight ?? 0) > 0;
  return {
    ...(hasViewport
      ? {
          viewport: {
            width: input.viewportWidth!,
            height: input.viewportHeight!,
            ...(Number.isFinite(input.zoom) && (input.zoom ?? 0) > 0 ? { zoom: input.zoom } : {}),
            ...(input.visibleSlideIds?.length ? { visibleSlideIds: input.visibleSlideIds } : {}),
          },
        }
      : {}),
    ...(Number.isInteger(input.currentSlideIndex) && (input.currentSlideIndex ?? -1) >= 0
      ? { currentSlide: { ...(input.currentSlideId ? { slideId: input.currentSlideId } : {}), index: input.currentSlideIndex! } }
      : {}),
    assetIds: (input.assets ?? []).filter((asset) => asset.status === "ready" || asset.status === "scanned").map((asset) => asset.id),
  };
}
