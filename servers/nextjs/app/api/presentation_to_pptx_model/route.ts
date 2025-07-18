import { ApiError } from "@/models/errors";
import { NextRequest, NextResponse } from "next/server";
import puppeteer, { ElementHandle } from "puppeteer";
import { ElementAttributes } from "@/types/element_attibutes";


export async function GET(request: NextRequest) {

  try {
    const id = await getPresentationId(request);
    const slides = await getSlides(id);
    const slide = slides[0];
    const attributes = await getAllChildElementsAttributes(slide);
    console.log(attributes);

    // Temporary
    return NextResponse.json({
      attributes: attributes,
    });
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

async function getSlidesWrapper(id: string): Promise<ElementHandle<Element>> {
  const page = await getPresentationPage(id);
  const slides_wrapper = await page.$("#presentation-slides-wrapper");
  if (!slides_wrapper) {
    throw new ApiError("Presentation slides not found");
  }
  return slides_wrapper;
}

async function getSlides(id: string) {
  const slides_wrapper = await getSlidesWrapper(id);
  const slides = await slides_wrapper.$$(":scope > div > div");
  return slides;
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

    const computedStyles = window.getComputedStyle(el);

    // Parse position and dimensions
    const rect = el.getBoundingClientRect();
    const position = {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
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
    };

    if (boxShadow && boxShadow !== 'none') {
      const shadowParts = boxShadow.split(' ');
      if (shadowParts.length >= 4) {
        const offsetX = parseFloat(shadowParts[0]);
        const offsetY = parseFloat(shadowParts[1]);
        shadow = {
          offset: (!isNaN(offsetX) && !isNaN(offsetY)) ? [offsetX, offsetY] as [number, number] : undefined,
          color: colorToHex(shadowParts[3]),
          opacity: 1,
        };
      }
    }

    // Parse font
    const fontSize = parseFloat(computedStyles.fontSize);
    const fontWeight = parseInt(computedStyles.fontWeight);
    const fontColor = colorToHex(computedStyles.color);
    const font = {
      size: isNaN(fontSize) ? undefined : fontSize,
      weight: isNaN(fontWeight) ? undefined : fontWeight,
      color: fontColor,
    };

    // Parse margin
    const marginTop = parseFloat(computedStyles.marginTop);
    const marginBottom = parseFloat(computedStyles.marginBottom);
    const marginLeft = parseFloat(computedStyles.marginLeft);
    const marginRight = parseFloat(computedStyles.marginRight);
    const margin = {
      top: isNaN(marginTop) ? undefined : marginTop,
      bottom: isNaN(marginBottom) ? undefined : marginBottom,
      left: isNaN(marginLeft) ? undefined : marginLeft,
      right: isNaN(marginRight) ? undefined : marginRight,
    };

    // Parse padding
    const paddingTop = parseFloat(computedStyles.paddingTop);
    const paddingBottom = parseFloat(computedStyles.paddingBottom);
    const paddingLeft = parseFloat(computedStyles.paddingLeft);
    const paddingRight = parseFloat(computedStyles.paddingRight);
    const padding = {
      top: isNaN(paddingTop) ? undefined : paddingTop,
      bottom: isNaN(paddingBottom) ? undefined : paddingBottom,
      left: isNaN(paddingLeft) ? undefined : paddingLeft,
      right: isNaN(paddingRight) ? undefined : paddingRight,
    };

    return {
      tagName: el.tagName.toLowerCase(),
      id: el.id || undefined,
      className: el.className || undefined,
      innerText: el.textContent || undefined,
      background,
      border,
      shadow,
      font,
      position,
      margin,
      padding,
    };
  });
  return attributes;
}

async function getAllChildElementsAttributes(element: ElementHandle<Element>): Promise<ElementAttributes[]> {
  // Get the root element's bounding rect for relative positioning
  const rootRect = await element.evaluate((el) => el.getBoundingClientRect());
  
  // Get all child elements as ElementHandles
  const childElementHandles = await element.$$(':scope *');
  
  // Get attributes for each child element using getElementAttributes
  const attributesPromises = childElementHandles.map(async (childElementHandle) => {
    const attributes = await getElementAttributes(childElementHandle);
    
    // Convert positions to relative positions
    if (attributes.position && attributes.position.left !== undefined && attributes.position.top !== undefined) {
      attributes.position = {
        left: attributes.position.left - rootRect.left,
        top: attributes.position.top - rootRect.top,
        width: attributes.position.width,
        height: attributes.position.height,
      };
    }
    
    return attributes;
  });

  return Promise.all(attributesPromises);
}