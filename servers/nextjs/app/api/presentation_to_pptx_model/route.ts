import { ApiError } from "@/models/errors";
import { NextRequest, NextResponse } from "next/server";
import puppeteer, { ElementHandle } from "puppeteer";
import { ElementAttributes, SlideAttributesResult } from "@/types/element_attibutes";
import { convertElementAttributesToPptxSlides } from "@/utils/pptx_models_utils";
import { PptxPresentationModel } from "@/types/pptx_models";


export async function GET(request: NextRequest) {

  try {
    const id = await getPresentationId(request);
    const slides = await getSlides(id);
    const slides_attributes = await getSlidesAttributes(slides);
    const slides_pptx_models = convertElementAttributesToPptxSlides(slides_attributes.elements, slides_attributes.backgroundColors);
    const presentation_pptx_model: PptxPresentationModel = {
      slides: slides_pptx_models,
    };
    return NextResponse.json(presentation_pptx_model);
  } catch (error: any) {
    console.error(error);
    if (error instanceof ApiError) {
      return NextResponse.json(error, { status: 400 });
    }
    return NextResponse.json({ detail: `Internal server error: ${error.message}` }, { status: 500 });
  }
}

async function getPresentationId(request: NextRequest) {
  const id = request.nextUrl.searchParams.get("id");
  if (!id) {
    throw new ApiError("Presentation ID not found");
  }
  return id;
}

async function getSlidesAttributes(slides: ElementHandle<Element>[]) {
  const slideResults = await Promise.all(slides.map(async (slide) => {
    return await getAllChildElementsAttributes(slide);
  }));

  // Extract elements and background colors from each slide result
  const elements = slideResults.map(result => result.elements);
  const backgroundColors = slideResults.map(result => result.backgroundColor);

  return {
    elements,
    backgroundColors
  };
}


async function getSlides(id: string) {
  const slides_wrapper = await getSlidesWrapper(id);
  const slides = await slides_wrapper.$$(":scope > div > div");
  return slides;
}

async function getSlidesWrapper(id: string): Promise<ElementHandle<Element>> {
  const page = await getPresentationPage(id);
  const slides_wrapper = await page.$("#presentation-slides-wrapper");
  if (!slides_wrapper) {
    throw new ApiError("Presentation slides not found");
  }
  return slides_wrapper;
}


async function getPresentationPage(id: string) {
  const browser = await puppeteer.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1640, height: 720, deviceScaleFactor: 1 });
  await page.goto(`http://localhost/presentation?id=${id}`, {
    waitUntil: "networkidle0",
    timeout: 60000,
  });
  return page;
}


async function getAllChildElementsAttributes(element: ElementHandle<Element>): Promise<SlideAttributesResult> {
  // Get the root element's bounding rect for relative positioning
  const rootRect = await element.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    return {
      left: isFinite(rect.left) ? rect.left : 0,
      top: isFinite(rect.top) ? rect.top : 0,
      width: isFinite(rect.width) ? rect.width : 0,
      height: isFinite(rect.height) ? rect.height : 0,
    };
  });

  // Get all child elements as ElementHandles
  const childElementHandles = await element.$$(':scope *');

  // Get attributes and depth for each child element
  const attributesPromises = childElementHandles.map(async (childElementHandle) => {
    const attributes = await getElementAttributes(childElementHandle);

    // Calculate the depth of the element in the DOM tree
    const depth = await childElementHandle.evaluate((el) => {
      let depth = 0;
      let current = el;
      while (current.parentElement) {
        depth++;
        current = current.parentElement;
      }
      return depth;
    });

    // Convert positions to relative positions
    if (attributes.position && attributes.position.left !== undefined && attributes.position.top !== undefined) {
      attributes.position = {
        left: attributes.position.left - rootRect.left,
        top: attributes.position.top - rootRect.top,
        width: attributes.position.width,
        height: attributes.position.height,
      };
    }

    return { attributes, depth };
  });

  const allResults = await Promise.all(attributesPromises);

  // Extract background color from elements whose position is the same as root element
  let backgroundColor: string | undefined;
  const elementsWithRootPosition = allResults.filter(({ attributes }) => {
    return attributes.position &&
      attributes.position.left === 0 &&
      attributes.position.top === 0 &&
      attributes.position.width === rootRect.width &&
      attributes.position.height === rootRect.height;
  });

  // Get the background color from the first element with root position that has a background
  for (const { attributes } of elementsWithRootPosition) {
    if (attributes.background && attributes.background.color) {
      backgroundColor = attributes.background.color;
      break;
    }
  }

  // Filter out elements with no meaningful styling and elements with same position as root
  const filteredResults = allResults.filter(({ attributes }) => {
    // Check if element has any meaningful styling or content
    const hasBackground = attributes.background && attributes.background.color;
    const hasBorder = attributes.border && attributes.border.color;
    const hasShadow = attributes.shadow && attributes.shadow.color;
    const hasText = attributes.innerText && attributes.innerText.trim().length > 0;

    // Check if element position is the same as root (exclude these elements)
    const isRootPosition = attributes.position &&
      attributes.position.left === 0 &&
      attributes.position.top === 0 &&
      attributes.position.width === rootRect.width &&
      attributes.position.height === rootRect.height;

    // Return true if element has at least one of these properties AND is not at root position
    return (hasBackground || hasBorder || hasShadow || hasText) && !isRootPosition;
  });

  // Sort elements by z-index first, then by depth if z-index is not provided
  const sortedElements = filteredResults
    .sort((a, b) => {
      const zIndexA = a.attributes.zIndex || 0;
      const zIndexB = b.attributes.zIndex || 0;

      // If both elements have the same z-index (including 0), sort by depth
      if (zIndexA === zIndexB) {
        return b.depth - a.depth; // Higher depth first (children before parents)
      }

      // Otherwise sort by z-index (higher z-index first, as elements below come first)
      return zIndexB - zIndexA;
    })
    .map(({ attributes }) => attributes); // Extract just the attributes

  return {
    elements: sortedElements,
    backgroundColor
  };
}


async function getElementAttributes(element: ElementHandle<Element>): Promise<ElementAttributes> {
  const attributes = await element.evaluate((el) => {
    // Helper function to convert color to hex
    function colorToHex(color: string): string | undefined {
      if (!color || color === 'transparent' || color === 'rgba(0, 0, 0, 0)') {
        return undefined;
      }

      // Create a temporary canvas to convert colors to hex
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return color;

      ctx.fillStyle = color;
      return ctx.fillStyle;
    }

    // Helper function to check if element has only text nodes as direct children
    function hasOnlyTextNodes(el: Element): boolean {
      const children = el.childNodes;
      for (let i = 0; i < children.length; i++) {
        const child = children[i];
        // If any child is an element node (not a text node), return false
        if (child.nodeType === Node.ELEMENT_NODE) {
          return false;
        }
      }
      return true;
    }

    const computedStyles = window.getComputedStyle(el);

    // Parse position and dimensions
    const rect = el.getBoundingClientRect();
    const position = {
      left: isFinite(rect.left) ? rect.left : 0,
      top: isFinite(rect.top) ? rect.top : 0,
      width: isFinite(rect.width) ? rect.width : 0,
      height: isFinite(rect.height) ? rect.height : 0,
    };

    // Parse background
    const backgroundColor = colorToHex(computedStyles.backgroundColor);
    const backgroundOpacity = parseFloat(computedStyles.opacity);
    const background = {
      color: backgroundColor,
      opacity: isNaN(backgroundOpacity) ? undefined : backgroundOpacity,
    };

    // Parse border
    const borderColor = colorToHex(computedStyles.borderColor);
    const borderWidth = parseFloat(computedStyles.borderWidth);
    const border = borderWidth === 0 ? undefined : {
      color: borderColor,
      width: isNaN(borderWidth) ? undefined : borderWidth,
    };

    // Parse shadow (box-shadow)
    const boxShadow = computedStyles.boxShadow;
    let shadow = {
      offset: undefined as [number, number] | undefined,
      color: undefined as string | undefined,
      opacity: undefined as number | undefined,
      radius: undefined as number | undefined,
      angle: undefined as number | undefined,
    };

    if (boxShadow && boxShadow !== 'none') {
      const shadowParts = boxShadow.split(' ');
      if (shadowParts.length >= 4) {
        const offsetX = parseFloat(shadowParts[0]);
        const offsetY = parseFloat(shadowParts[1]);
        const blurRadius = parseFloat(shadowParts[2]);
        shadow = {
          offset: (!isNaN(offsetX) && !isNaN(offsetY)) ? [offsetX, offsetY] as [number, number] : undefined,
          color: colorToHex(shadowParts[3]),
          opacity: 1,
          radius: !isNaN(blurRadius) ? blurRadius : undefined,
          angle: !isNaN(offsetX) && !isNaN(offsetY) ? Math.atan2(offsetY, offsetX) * (180 / Math.PI) : undefined,
        };
      }
    }

    // Parse font
    const fontSize = parseFloat(computedStyles.fontSize);
    const fontWeight = parseInt(computedStyles.fontWeight);
    const fontColor = colorToHex(computedStyles.color);
    const fontFamily = computedStyles.fontFamily;
    const fontStyle = computedStyles.fontStyle;

    // Extract only the first font from font-family (e.g., "Hack, sans-serif" -> "Hack")
    let fontName = undefined;
    if (fontFamily !== 'initial') {
      const firstFont = fontFamily.split(',')[0].trim().replace(/['"]/g, '');
      fontName = firstFont;
    }

    const font = {
      name: fontName,
      size: isNaN(fontSize) ? undefined : fontSize,
      weight: isNaN(fontWeight) ? undefined : fontWeight,
      color: fontColor,
      italic: fontStyle === 'italic',
    };

    // Parse margin
    const marginTop = parseFloat(computedStyles.marginTop);
    const marginBottom = parseFloat(computedStyles.marginBottom);
    const marginLeft = parseFloat(computedStyles.marginLeft);
    const marginRight = parseFloat(computedStyles.marginRight);
    const marginObj = {
      top: isNaN(marginTop) ? undefined : marginTop,
      bottom: isNaN(marginBottom) ? undefined : marginBottom,
      left: isNaN(marginLeft) ? undefined : marginLeft,
      right: isNaN(marginRight) ? undefined : marginRight,
    };

    // Set margin as undefined if all fields are 0
    const margin = (marginObj.top === 0 && marginObj.bottom === 0 && marginObj.left === 0 && marginObj.right === 0)
      ? undefined
      : marginObj;

    // Parse padding
    const paddingTop = parseFloat(computedStyles.paddingTop);
    const paddingBottom = parseFloat(computedStyles.paddingBottom);
    const paddingLeft = parseFloat(computedStyles.paddingLeft);
    const paddingRight = parseFloat(computedStyles.paddingRight);
    const paddingObj = {
      top: isNaN(paddingTop) ? undefined : paddingTop,
      bottom: isNaN(paddingBottom) ? undefined : paddingBottom,
      left: isNaN(paddingLeft) ? undefined : paddingLeft,
      right: isNaN(paddingRight) ? undefined : paddingRight,
    };

    // Set padding as undefined if all fields are 0
    const padding = (paddingObj.top === 0 && paddingObj.bottom === 0 && paddingObj.left === 0 && paddingObj.right === 0)
      ? undefined
      : paddingObj;

    // Only include innerText if the element has only text nodes as direct children
    const innerText = hasOnlyTextNodes(el) ? (el.textContent || undefined) : undefined;

    // Parse z-index
    const zIndex = parseInt(computedStyles.zIndex);
    const zIndexValue = isNaN(zIndex) ? 0 : zIndex;

    // Parse additional attributes
    const textAlign = computedStyles.textAlign as 'left' | 'center' | 'right' | 'justify';
    const borderRadius = computedStyles.borderRadius;
    const objectFit = computedStyles.objectFit as 'contain' | 'cover' | 'fill' | undefined;
    const imageSrc = (el as HTMLImageElement).src;

    // Parse border radius
    let borderRadiusValue: number | number[] | undefined;
    if (borderRadius && borderRadius !== '0px') {
      const radiusParts = borderRadius.split(' ').map(part => parseFloat(part));
      if (radiusParts.length === 1) {
        borderRadiusValue = radiusParts[0];
      } else if (radiusParts.length === 4) {
        borderRadiusValue = radiusParts;
      }
    }

    // Determine shape for images
    let shape: 'rectangle' | 'circle' | undefined;
    if (el.tagName.toLowerCase() === 'img') {
      shape = borderRadiusValue === 50 ? 'circle' : 'rectangle';
    }

    // Check for text wrap
    const textWrap = computedStyles.whiteSpace !== 'nowrap';

    return {
      tagName: el.tagName.toLowerCase(),
      id: el.id || undefined,
      className: el.className || undefined,
      innerText,
      background,
      border,
      shadow,
      font,
      position,
      margin,
      padding,
      zIndex: zIndexValue,
      textAlign: textAlign !== 'left' ? textAlign : undefined,
      borderRadius: borderRadiusValue,
      imageSrc: imageSrc || undefined,
      objectFit,
      clip: false, // Default value
      overlay: undefined,
      shape,
      connectorType: undefined,
      textWrap,
    };
  });
  return attributes;
}
