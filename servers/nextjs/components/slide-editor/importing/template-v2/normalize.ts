/**
 * Stage 2 (normalize) — post-mapping passes over adapted elements. Today this
 * is the author-info-card widening, which cannot run before mapping because it
 * needs resolved fills and frames.
 */
import {
  EDITOR_STAGE_WIDTH,
  type GroupElement,
  type Slide,
  type SlideElement,
} from "@/components/slide-editor/types";
import { MIN_ELEMENT_SIZE } from "@/components/slide-editor/importing/template-v2/values";

export function normalizeTemplateV2Slide(slide: Slide): Slide {
  return {
    ...slide,
    elements: slide.elements.map(normalizeTemplateV2Element),
  };
}

export function normalizeTemplateV2Element(element: SlideElement): SlideElement {
  if ("children" in element && Array.isArray(element.children)) {
    const next = {
      ...element,
      children: element.children.map(normalizeTemplateV2Element),
    } as SlideElement;
    return next.type === "group" ? normalizeAuthorInfoCardGroup(next) : next;
  }

  if (element.type === "container" && element.child) {
    return {
      ...element,
      child: normalizeTemplateV2Element(element.child),
    };
  }

  return element;
}

function normalizeAuthorInfoCardGroup(group: GroupElement): GroupElement {
  if (!isAuthorInfoCard(group)) return group;

  const cardRight = authorInfoCardRightEdge(group);
  let changed = false;
  const children = group.children.map((child) => {
    if (!isAuthorInfoText(child) || !child.position || !child.size) {
      return child;
    }

    const availableWidth = Math.max(
      MIN_ELEMENT_SIZE,
      cardRight - child.position.x - 24,
    );
    if (availableWidth <= child.size.width + 1) return child;

    changed = true;
    return {
      ...child,
      size: {
        ...child.size,
        width: Math.min(EDITOR_STAGE_WIDTH, availableWidth),
      },
    };
  });

  return changed ? { ...group, children } : group;
}

function isAuthorInfoCard(group: GroupElement) {
  return (
    group.component_id === "author_info_card" ||
    group.component_slot === "author_info_card" ||
    group.component_instance_id?.startsWith("author_info_card:")
  );
}

function authorInfoCardRightEdge(group: GroupElement) {
  const backgroundRightEdge = group.children
    .filter((child): child is Extract<SlideElement, { type: "vector" }> =>
      child.type === "vector",
    )
    .map(vectorBackgroundRightEdge)
    .find((rightEdge): rightEdge is number => rightEdge != null);

  if (backgroundRightEdge != null) return backgroundRightEdge;

  return group.size.width;
}

function vectorBackgroundRightEdge(
  element: Extract<SlideElement, { type: "vector" }>,
) {
  if (element.points.length === 0 || !element.fill?.color) return null;
  const minX = Math.min(...element.points.map((point) => point.x));
  const minY = Math.min(...element.points.map((point) => point.y));
  const maxX = Math.max(...element.points.map((point) => point.x));
  if (minX > 0.05 || minY > 0.05 || maxX <= 0) return null;
  return maxX;
}

function isAuthorInfoText(
  element: SlideElement,
): element is Extract<SlideElement, { type: "text" }> {
  return (
    element.type === "text" &&
    (element.component_slot === "author_name" || element.component_slot === "date")
  );
}
