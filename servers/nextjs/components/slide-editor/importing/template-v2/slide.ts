/**
 * Stage 4 — assembling adapted elements into a slide: the fallback element,
 * background detection, and the layout/component entry points that run parse →
 * map → normalize in order.
 */
import {
  EDITOR_STAGE_HEIGHT,
  EDITOR_STAGE_WIDTH,
  type Slide,
  type SlideElement,
} from "@/components/slide-editor/types";
import { MIN_ELEMENT_SIZE } from "@/components/slide-editor/importing/template-v2/values";
import {
  componentToGroupElement,
  extractLayoutElements,
  titleFromLayout,
  type TemplateV2Layout,
} from "@/components/slide-editor/importing/template-v2/raw-layout";
import { adaptElement } from "@/components/slide-editor/importing/template-v2/elements";
import { adaptedElementBounds } from "@/components/slide-editor/importing/template-v2/element-bounds";
import {
  normalizeTemplateV2Element,
  normalizeTemplateV2Slide,
} from "@/components/slide-editor/importing/template-v2/normalize";

export function adaptTemplateV2LayoutToSlide(
  layout: TemplateV2Layout,
  index = 0,
): Slide {
  return adaptLayoutToSlide(layout, index);
}

export function adaptTemplateV2ComponentToElement(
  component: unknown,
  componentIndex = 0,
): SlideElement | null {
  const rawGroup = componentToGroupElement(component, componentIndex);
  if (!rawGroup) return null;

  const element = adaptElement(rawGroup);
  return element ? normalizeTemplateV2Element(element) : null;
}

export function adaptLayoutToSlide(
  layout: TemplateV2Layout,
  index: number,
): Slide {
  const rawElements = extractLayoutElements(layout);
  const elements = rawElements
    .slice(0, 80)
    .map((element) => adaptElement(element))
    .filter((element): element is SlideElement => Boolean(element));

  const slideElements =
    elements.length > 0 ? elements : [invisibleFallbackElement()];

  return normalizeTemplateV2Slide({
    title: titleFromLayout(layout, index),
    background: backgroundFromElements(slideElements),
    elements: slideElements,
  });
}

function invisibleFallbackElement(): SlideElement {
  return {
    type: "vector",
    points: [
      { x: 0, y: 0 },
      { x: MIN_ELEMENT_SIZE, y: 0 },
      { x: MIN_ELEMENT_SIZE, y: MIN_ELEMENT_SIZE },
      { x: 0, y: MIN_ELEMENT_SIZE },
    ],
    closed: true,
    fill: { color: "FFFFFF" },
    opacity: 0,
  };
}

function backgroundFromElements(elements: SlideElement[]) {
  const background = findBackgroundShape(elements, 0, 0);

  return background && "fill" in background && background.fill?.color
    ? background.fill.color
    : "FFFFFF";
}

function findBackgroundShape(
  elements: SlideElement[],
  offsetX: number,
  offsetY: number,
): SlideElement | null {
  for (const element of elements) {
    const bounds = adaptedElementBounds(element);
    const x = offsetX + bounds.x;
    const y = offsetY + bounds.y;
    if (isFullSlideFilledShape(element, x, y, bounds)) {
      return element;
    }

    if (
      element.type === "group" ||
      element.type === "flex" ||
      element.type === "grid"
    ) {
      const background = findBackgroundShape(element.children, x, y);
      if (background) return background;
    }

    if (element.type === "container" && element.child) {
      const background = findBackgroundShape([element.child], x, y);
      if (background) return background;
    }
  }

  return null;
}

function isFullSlideFilledShape(
  element: SlideElement,
  x: number,
  y: number,
  bounds: { width: number; height: number },
) {
  if (
    element.type !== "vector"
  ) return false;
  return (
    x === 0 &&
    y === 0 &&
    bounds.width === EDITOR_STAGE_WIDTH &&
    bounds.height === EDITOR_STAGE_HEIGHT &&
    Boolean(element.fill?.color)
  );
}
