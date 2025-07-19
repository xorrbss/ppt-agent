import { ApiError } from "@/models/errors";
import { NextRequest, NextResponse } from "next/server";
import puppeteer, { Browser, ElementHandle } from "puppeteer";
import { ElementAttributes, SlideAttributesResult } from "@/types/element_attibutes";
import { convertElementAttributesToPptxSlides } from "@/utils/pptx_models_utils";
import { PptxPresentationModel } from "@/types/pptx_models";

// Interface for getAllChildElementsAttributes function arguments
interface GetAllChildElementsAttributesArgs {
  element: ElementHandle<Element>;
  rootRect?: { left: number; top: number; width: number; height: number } | null;
  depth?: number;
  inheritedFont?: ElementAttributes['font'];
  inheritedBackground?: ElementAttributes['background'];
}


export async function GET(request: NextRequest) {
  let browser: Browser | null = null;
  try {
    const id = await getPresentationId(request);
    browser = await puppeteer.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });

    const slides = await getSlides(browser, id);
    const slides_attributes = await getSlidesAttributes(slides);
    const slides_pptx_models = convertElementAttributesToPptxSlides(slides_attributes.elements, slides_attributes.backgroundColors);
    const presentation_pptx_model: PptxPresentationModel = {
      slides: slides_pptx_models,
    };

    if (browser) {
      await browser.close();
    }

    return NextResponse.json(presentation_pptx_model);
  } catch (error: any) {
    console.error(error);
    if (browser) {
      await browser.close();
    }
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
    return await getAllChildElementsAttributes({ element: slide });
  }));

  const elements = slideResults.map(result => result.elements);
  const backgroundColors = slideResults.map(result => result.backgroundColor);

  return {
    elements,
    backgroundColors
  };
}


async function getSlides(browser: Browser, id: string) {
  const slides_wrapper = await getSlidesWrapper(browser, id);
  const slides = await slides_wrapper.$$(":scope > div > div");
  return slides;
}

async function getSlidesWrapper(browser: Browser, id: string): Promise<ElementHandle<Element>> {
  const page = await getPresentationPage(browser, id);
  const slides_wrapper = await page.$("#presentation-slides-wrapper");
  if (!slides_wrapper) {
    throw new ApiError("Presentation slides not found");
  }
  return slides_wrapper;
}


async function getPresentationPage(browser: Browser, id: string) {
  const page = await browser.newPage();

  page.on('console', (msg) => {
    const type = msg.type();
    const text = msg.text();
  });

  await page.setViewport({ width: 1640, height: 720, deviceScaleFactor: 1 });
  await page.goto(`http://localhost/presentation?id=${id}`, {
    waitUntil: "networkidle0",
    timeout: 60000,
  });
  return page;
}


async function getAllChildElementsAttributes({ element, rootRect = null, depth = 0, inheritedFont, inheritedBackground }: GetAllChildElementsAttributesArgs): Promise<SlideAttributesResult> {
  // Get rootRect if not provided (first call)
  const currentRootRect = rootRect || await element.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    return {
      left: isFinite(rect.left) ? rect.left : 0,
      top: isFinite(rect.top) ? rect.top : 0,
      width: isFinite(rect.width) ? rect.width : 0,
      height: isFinite(rect.height) ? rect.height : 0,
    };
  });

  // Get direct children only (not all descendants)
  const directChildElementHandles = await element.$$(':scope > *');

  const allResults: { attributes: ElementAttributes; depth: number }[] = [];

  // Process direct children recursively
  for (const childElementHandle of directChildElementHandles) {
    // Get attributes for current child
    const attributes = await getElementAttributes(childElementHandle);

    // Apply inherited font only on elements that have direct text
    if (inheritedFont && !attributes.font && attributes.innerText && attributes.innerText.trim().length > 0) {
      attributes.font = inheritedFont;
    }
    // Apply inherited background only on elements that have shadow
    if (inheritedBackground && !attributes.background && attributes.shadow) {
      attributes.background = inheritedBackground;
    }

    // Adjust position relative to root
    if (attributes.position && attributes.position.left !== undefined && attributes.position.top !== undefined) {
      attributes.position = {
        left: attributes.position.left - currentRootRect.left,
        top: attributes.position.top - currentRootRect.top,
        width: attributes.position.width,
        height: attributes.position.height,
      };
    }

    // Add current child to results
    allResults.push({ attributes, depth });

    // Recursively process children of this child
    const childResults = await getAllChildElementsAttributes({
      element: childElementHandle,
      rootRect: currentRootRect,
      depth: depth + 1,
      inheritedFont: attributes.font || inheritedFont,
      inheritedBackground: attributes.background || inheritedBackground,
    });
    allResults.push(...childResults.elements.map(attr => ({ attributes: attr, depth: depth + 1 })));
  }

  // Find background color from elements with root position (only in first call)
  let backgroundColor: string | undefined;
  if (!rootRect) {
    const elementsWithRootPosition = allResults.filter(({ attributes }) => {
      return attributes.position &&
        attributes.position.left === 0 &&
        attributes.position.top === 0 &&
        attributes.position.width === currentRootRect.width &&
        attributes.position.height === currentRootRect.height;
    });

    for (const { attributes } of elementsWithRootPosition) {
      if (attributes.background && attributes.background.color) {
        backgroundColor = attributes.background.color;
        break;
      }
    }
  }

  // Filter results (only in first call)
  const filteredResults = !rootRect ? allResults.filter(({ attributes }) => {
    const hasBackground = attributes.background && attributes.background.color;
    const hasBorder = attributes.border && attributes.border.color;
    const hasShadow = attributes.shadow && attributes.shadow.color;
    const hasText = attributes.innerText && attributes.innerText.trim().length > 0;
    const hasImage = attributes.imageSrc;

    const isRootPosition = attributes.position &&
      attributes.position.left === 0 &&
      attributes.position.top === 0 &&
      attributes.position.width === currentRootRect.width &&
      attributes.position.height === currentRootRect.height;

    const hasOtherProperties = hasBackground || hasBorder || hasShadow || hasText || hasImage;
    return hasOtherProperties && !isRootPosition;
  }) : allResults;

  if (!rootRect) {
    const sortedElements = filteredResults
      .sort((a, b) => {
        const zIndexA = a.attributes.zIndex || 0;
        const zIndexB = b.attributes.zIndex || 0;

        if (zIndexA === zIndexB) {
          return b.depth - a.depth;
        }

        return zIndexB - zIndexA;
      })
      .map(({ attributes }) => {
        // Set background color to backgroundColor for elements that have shadow but no background color
        if (attributes.shadow && attributes.shadow.color && (!attributes.background || !attributes.background.color) && backgroundColor) {
          attributes.background = {
            color: backgroundColor,
            opacity: undefined
          };
        }
        return attributes;
      });

    return {
      elements: sortedElements,
      backgroundColor
    };
  } else {
    return {
      elements: filteredResults.map(({ attributes }) => attributes),
      backgroundColor
    };
  }
}


// Do not edit this function, it is used to get the attributes of an element
async function getElementAttributes(element: ElementHandle<Element>): Promise<ElementAttributes> {

  const attributes = await element.evaluate((el: Element) => {

    function colorToHex(color: string): { hex: string | undefined; opacity: number | undefined } {
      if (!color || color === 'transparent' || color === 'rgba(0, 0, 0, 0)') {
        return { hex: undefined, opacity: undefined };
      }

      if (color.startsWith('rgba(') || color.startsWith('hsla(')) {
        const match = color.match(/rgba?\(([^)]+)\)|hsla?\(([^)]+)\)/);
        if (match) {
          const values = match[1] || match[2];
          const parts = values.split(',').map(part => part.trim());

          if (parts.length >= 4) {
            const opacity = parseFloat(parts[3]);
            const rgbColor = color.replace(/rgba?\(|hsla?\(|\)/g, '').split(',').slice(0, 3).join(',');
            const rgbString = color.startsWith('rgba') ? `rgb(${rgbColor})` : `hsl(${rgbColor})`;

            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            if (ctx) {
              ctx.fillStyle = rgbString;
              const hexColor = ctx.fillStyle;
              const hex = hexColor.startsWith('#') ? hexColor.substring(1) : hexColor;
              const result = { hex, opacity: isNaN(opacity) ? undefined : opacity };

              return result;
            }
          }
        }
      }

      if (color.startsWith('rgb(') || color.startsWith('hsl(')) {
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        if (ctx) {
          ctx.fillStyle = color;
          const hexColor = ctx.fillStyle;
          const hex = hexColor.startsWith('#') ? hexColor.substring(1) : hexColor;
          return { hex, opacity: undefined };
        }
      }

      if (color.startsWith('#')) {
        const hex = color.substring(1);
        return { hex, opacity: undefined };
      }

      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      if (!ctx) return { hex: color, opacity: undefined };

      ctx.fillStyle = color;
      const hexColor = ctx.fillStyle;
      const hex = hexColor.startsWith('#') ? hexColor.substring(1) : hexColor;
      const result = { hex, opacity: undefined };

      return result;
    }

    function hasOnlyTextNodes(el: Element): boolean {
      const children = el.childNodes;
      for (let i = 0; i < children.length; i++) {
        const child = children[i];
        if (child.nodeType === Node.ELEMENT_NODE) {
          return false;
        }
      }
      return true;
    }

    function parsePosition(el: Element) {
      const rect = el.getBoundingClientRect();
      return {
        left: isFinite(rect.left) ? rect.left : 0,
        top: isFinite(rect.top) ? rect.top : 0,
        width: isFinite(rect.width) ? rect.width : 0,
        height: isFinite(rect.height) ? rect.height : 0,
      };
    }



    function parseBackground(computedStyles: CSSStyleDeclaration) {
      const backgroundColorResult = colorToHex(computedStyles.backgroundColor);

      return {
        color: backgroundColorResult.hex,
        opacity: backgroundColorResult.opacity
      };
    }

    function parseBorder(computedStyles: CSSStyleDeclaration) {
      const borderColorResult = colorToHex(computedStyles.borderColor);
      const borderWidth = parseFloat(computedStyles.borderWidth);
      return borderWidth === 0 ? undefined : {
        color: borderColorResult.hex,
        width: isNaN(borderWidth) ? undefined : borderWidth,
      };
    }

    function parseShadow(computedStyles: CSSStyleDeclaration) {
      const boxShadow = computedStyles.boxShadow;
      if (boxShadow !== 'none') {
      }
      let shadow: {
        offset?: [number, number];
        color?: string;
        opacity?: number;
        radius?: number;
        angle?: number;
        spread?: number;
        inset?: boolean;
      } = {};

      if (boxShadow && boxShadow !== 'none') {
        // Handle multiple shadows (comma-separated) - find the first meaningful one
        // Need to split on commas but not inside function calls like rgba()
        const shadows: string[] = [];
        let currentShadow = '';
        let parenCount = 0;

        for (let i = 0; i < boxShadow.length; i++) {
          const char = boxShadow[i];
          if (char === '(') {
            parenCount++;
          } else if (char === ')') {
            parenCount--;
          } else if (char === ',' && parenCount === 0) {
            // This comma is outside of any function call, so it separates shadows
            shadows.push(currentShadow.trim());
            currentShadow = '';
            continue;
          }
          currentShadow += char;
        }

        // Add the last shadow
        if (currentShadow.trim()) {
          shadows.push(currentShadow.trim());
        }



        let selectedShadow = '';
        let bestShadowScore = -1;

        for (let i = 0; i < shadows.length; i++) {
          const shadowStr = shadows[i];
          console.log(`Analyzing shadow ${i}: "${shadowStr}"`);

          // Parse the shadow to check if it has meaningful values
          const shadowParts = shadowStr.split(' ');
          const numericParts: number[] = [];
          const colorParts: string[] = [];
          let isInset = false;
          let currentColor = '';
          let inColorFunction = false;

          // Parse each part
          for (let j = 0; j < shadowParts.length; j++) {
            const part = shadowParts[j];
            const trimmedPart = part.trim();
            if (trimmedPart === '') continue;

            if (trimmedPart.toLowerCase() === 'inset') {
              isInset = true;
              continue;
            }

            // Check if this part starts a color function (rgba, rgb, hsl, hsla)
            if (trimmedPart.match(/^(rgba?|hsla?)\s*\(/i)) {
              inColorFunction = true;
              currentColor = trimmedPart;
              continue;
            }

            // If we're inside a color function, keep building it
            if (inColorFunction) {
              currentColor += ' ' + trimmedPart;

              // Check if we've reached the end of the color function
              const openParens = (currentColor.match(/\(/g) || []).length;
              const closeParens = (currentColor.match(/\)/g) || []).length;

              if (openParens <= closeParens) {
                // Color function is complete
                colorParts.push(currentColor);
                currentColor = '';
                inColorFunction = false;
              }
              continue;
            }

            const numericValue = parseFloat(trimmedPart);
            if (!isNaN(numericValue)) {
              numericParts.push(numericValue);
            } else {
              colorParts.push(trimmedPart);
            }
          }

          // Check if the color is not completely transparent using colorToHex
          let hasVisibleColor = false;
          if (colorParts.length > 0) {
            const shadowColor = colorParts.join(' ');
            const colorResult = colorToHex(shadowColor);
            hasVisibleColor = !!(colorResult.hex && colorResult.hex !== '000000' && colorResult.opacity !== 0);
          }

          // Check if we have any non-zero numeric values (offset, blur, or spread)
          const hasNonZeroValues = numericParts.some(value => value !== 0);

          // Calculate a score for this shadow (higher is better)
          let shadowScore = 0;
          if (hasNonZeroValues) {
            // Count non-zero numeric values
            shadowScore += numericParts.filter(value => value !== 0).length;
          }
          if (hasVisibleColor) {
            shadowScore += 2; // Bonus for visible color
          }

          // Select this shadow if it has a better score
          if ((hasNonZeroValues || hasVisibleColor) && shadowScore > bestShadowScore) {
            selectedShadow = shadowStr;
            bestShadowScore = shadowScore;
          }
        }

        // If no meaningful shadow found, use the first one
        if (!selectedShadow && shadows.length > 0) {
          selectedShadow = shadows[0];
        }

        if (selectedShadow) {

          // Parse the selected shadow
          const shadowParts = selectedShadow.split(' ');
          const numericParts: number[] = [];
          const colorParts: string[] = [];
          let isInset = false;
          let currentColor = '';
          let inColorFunction = false;

          // Parse each part
          for (let i = 0; i < shadowParts.length; i++) {
            const part = shadowParts[i];
            const trimmedPart = part.trim();
            if (trimmedPart === '') continue;

            if (trimmedPart.toLowerCase() === 'inset') {
              isInset = true;
              continue;
            }

            // Check if this part starts a color function (rgba, rgb, hsl, hsla)
            if (trimmedPart.match(/^(rgba?|hsla?)\s*\(/i)) {
              inColorFunction = true;
              currentColor = trimmedPart;
              continue;
            }

            // If we're inside a color function, keep building it
            if (inColorFunction) {
              currentColor += ' ' + trimmedPart;

              // Check if we've reached the end of the color function
              const openParens = (currentColor.match(/\(/g) || []).length;
              const closeParens = (currentColor.match(/\)/g) || []).length;

              if (openParens <= closeParens) {
                // Color function is complete
                colorParts.push(currentColor);
                currentColor = '';
                inColorFunction = false;
              }
              continue;
            }

            const numericValue = parseFloat(trimmedPart);
            if (!isNaN(numericValue)) {
              numericParts.push(numericValue);
            } else {
              colorParts.push(trimmedPart);
            }
          }

          // Handle different shadow formats
          if (numericParts.length >= 2) {
            const offsetX = numericParts[0];
            const offsetY = numericParts[1];
            const blurRadius = numericParts.length >= 3 ? numericParts[2] : 0;
            const spreadRadius = numericParts.length >= 4 ? numericParts[3] : 0;

            // Handle color - it can be anywhere in the parts
            let shadowColor = 'rgba(0, 0, 0, 0.3)'; // default color
            if (colorParts.length > 0) {
              shadowColor = colorParts.join(' ');
            }

            const shadowColorResult = colorToHex(shadowColor);

            // Create shadow object if we have any meaningful values or visible color
            const hasValidValues = offsetX !== 0 || offsetY !== 0 || blurRadius > 0 || spreadRadius !== 0 ||
              (shadowColorResult.hex && shadowColorResult.hex !== '000000' && shadowColorResult.opacity !== 0);

            if (hasValidValues) {
              shadow = {
                offset: [offsetX, offsetY],
                color: shadowColorResult.hex || '000000',
                opacity: shadowColorResult.opacity,
                radius: blurRadius,
                spread: spreadRadius,
                inset: isInset,
                angle: Math.atan2(offsetY, offsetX) * (180 / Math.PI),
              };
            }
          }
        }
      }

      return shadow;
    }

    function parseFont(computedStyles: CSSStyleDeclaration) {
      const fontSize = parseFloat(computedStyles.fontSize);
      const fontWeight = parseInt(computedStyles.fontWeight);
      const fontColorResult = colorToHex(computedStyles.color);
      const fontFamily = computedStyles.fontFamily;
      const fontStyle = computedStyles.fontStyle;

      let fontName = undefined;
      if (fontFamily !== 'initial') {
        const firstFont = fontFamily.split(',')[0].trim().replace(/['"]/g, '');
        fontName = firstFont;
      }

      return {
        name: fontName,
        size: isNaN(fontSize) ? undefined : fontSize,
        weight: isNaN(fontWeight) ? undefined : fontWeight,
        color: fontColorResult.hex,
        italic: fontStyle === 'italic',
      };
    }

    function parseMargin(computedStyles: CSSStyleDeclaration) {
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

      return (marginObj.top === 0 && marginObj.bottom === 0 && marginObj.left === 0 && marginObj.right === 0)
        ? undefined
        : marginObj;
    }

    function parsePadding(computedStyles: CSSStyleDeclaration) {
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

      return (paddingObj.top === 0 && paddingObj.bottom === 0 && paddingObj.left === 0 && paddingObj.right === 0)
        ? undefined
        : paddingObj;
    }

    function parseBorderRadius(computedStyles: CSSStyleDeclaration) {
      const borderRadius = computedStyles.borderRadius;
      let borderRadiusValue;

      if (borderRadius && borderRadius !== '0px') {
        const radiusParts = borderRadius.split(' ').map(part => parseFloat(part));
        if (radiusParts.length === 1) {
          borderRadiusValue = [radiusParts[0], radiusParts[0], radiusParts[0], radiusParts[0]];
        } else if (radiusParts.length === 2) {
          borderRadiusValue = [radiusParts[0], radiusParts[1], radiusParts[0], radiusParts[1]];
        } else if (radiusParts.length === 3) {
          borderRadiusValue = [radiusParts[0], radiusParts[1], radiusParts[2], radiusParts[1]];
        } else if (radiusParts.length === 4) {
          borderRadiusValue = radiusParts;
        }
      }

      return borderRadiusValue;
    }

    function parseShape(el: Element, borderRadiusValue: number[] | undefined) {
      if (el.tagName.toLowerCase() === 'img') {
        return borderRadiusValue && borderRadiusValue.length === 4 &&
          borderRadiusValue.every((radius: number) => radius === 50) ? 'circle' : 'rectangle';
      }
      return undefined;
    }

    function parseElementAttributes(el: Element) {
      const computedStyles = window.getComputedStyle(el);

      const position = parsePosition(el);

      const shadow = parseShadow(computedStyles);

      const background = parseBackground(computedStyles);

      const border = parseBorder(computedStyles);

      const font = parseFont(computedStyles);

      const margin = parseMargin(computedStyles);

      const padding = parsePadding(computedStyles);

      const innerText = hasOnlyTextNodes(el) ? (el.textContent || undefined) : undefined;

      const zIndex = parseInt(computedStyles.zIndex);
      const zIndexValue = isNaN(zIndex) ? 0 : zIndex;

      const textAlign = computedStyles.textAlign as 'left' | 'center' | 'right' | 'justify';
      const objectFit = computedStyles.objectFit as 'contain' | 'cover' | 'fill' | undefined;
      const imageSrc = (el as HTMLImageElement).src;

      const borderRadiusValue = parseBorderRadius(computedStyles);

      const shape = parseShape(el, borderRadiusValue) as 'rectangle' | 'circle' | undefined;

      const textWrap = computedStyles.whiteSpace !== 'nowrap';

      return {
        tagName: el.tagName.toLowerCase(),
        id: el.id || undefined,
        className: (el.className && typeof el.className === 'string') ? el.className : (el.className ? el.className.toString() : undefined),
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
        clip: false,
        overlay: undefined,
        shape,
        connectorType: undefined,
        textWrap,
      };
    }

    return parseElementAttributes(el);
  });
  return attributes;
}
