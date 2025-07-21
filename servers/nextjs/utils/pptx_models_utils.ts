import { ElementAttributes, SlideAttributesResult } from "@/types/element_attibutes";
import {
  PptxSlideModel,
  PptxTextBoxModel,
  PptxAutoShapeBoxModel,
  PptxPictureBoxModel,
  PptxConnectorModel,
  PptxPositionModel,
  PptxFillModel,
  PptxStrokeModel,
  PptxShadowModel,
  PptxFontModel,
  PptxParagraphModel,
  PptxPictureModel,
  PptxObjectFitModel,
  PptxBoxShapeEnum,
  PptxObjectFitEnum,
  PptxAlignment,
  PptxShapeType,
  PptxConnectorType
} from "@/types/pptx_models";

/**
 * Converts text alignment string to PptxAlignment enum value
 */
function convertTextAlignToPptxAlignment(textAlign?: string): PptxAlignment | undefined {
  if (!textAlign) return undefined;

  switch (textAlign.toLowerCase()) {
    case 'left':
      return PptxAlignment.LEFT;
    case 'center':
      return PptxAlignment.CENTER;
    case 'right':
      return PptxAlignment.RIGHT;
    case 'justify':
      return PptxAlignment.JUSTIFY;
    default:
      return PptxAlignment.LEFT;
  }
}

/**
 * Converts line height from pixels to relative format (e.g., 1.5)
 * If lineHeight is already a relative number (less than 10), return as is
 * Otherwise, convert from pixels to relative by dividing by font size
 */
function convertLineHeightToRelative(lineHeight?: number, fontSize?: number): number | undefined {
  if (!lineHeight) return undefined;

  let calculatedLineHeight = 1.2;
  // If lineHeight is already a relative number (typically between 1.0 and 3.0)
  if (lineHeight < 10) {
    calculatedLineHeight = lineHeight;
  }

  // If we have font size, convert from pixels to relative
  if (fontSize && fontSize > 0) {
    calculatedLineHeight = Math.round((lineHeight / fontSize) * 100) / 100; // Round to 2 decimal places
  }

  return calculatedLineHeight - 0.4 + (fontSize ?? 16) * 0.004;
}

/**
 * Converts SlideAttributesResult[] to PptxSlideModel[]
 * Each SlideAttributesResult represents elements on a slide
 */
export function convertElementAttributesToPptxSlides(
  slidesAttributes: SlideAttributesResult[]
): PptxSlideModel[] {
  return slidesAttributes.map((slideAttributes) => {
    const shapes = slideAttributes.elements.map(element => {
      return convertElementToPptxShape(element);
    }).filter(Boolean); // Remove any null/undefined shapes

    const slide: PptxSlideModel = {
      shapes: shapes as (PptxTextBoxModel | PptxAutoShapeBoxModel | PptxConnectorModel | PptxPictureBoxModel)[]
    };

    // Add background color if available
    if (slideAttributes.backgroundColor) {
      slide.background = {
        color: slideAttributes.backgroundColor,
        opacity: 1.0
      };
    }

    return slide;
  });
}

/**
 * Converts a single ElementAttributes to the appropriate PPTX shape model
 */
function convertElementToPptxShape(
  element: ElementAttributes
): PptxTextBoxModel | PptxAutoShapeBoxModel | PptxConnectorModel | PptxPictureBoxModel | null {
  // Skip elements without position
  if (!element.position) {
    return null;
  }

  // Check if it's an image element
  if (element.tagName === 'img' || (element.className && typeof element.className === 'string' && element.className.includes('image')) || element.imageSrc) {
    return convertToPictureBox(element);
  }

  // Check if it's a text element
  if (element.innerText && element.innerText.trim().length > 0) {
    return convertToTextBox(element);
  }

  // Check if it's a connector/line element
  if (element.tagName === 'hr' || (element.className && typeof element.className === 'string' && (element.className.includes('connector') || element.className.includes('line')))) {
    return convertToConnector(element);
  }

  // Default to auto shape box for other elements
  return convertToAutoShapeBox(element);
}

/**
 * Converts element to PptxTextBoxModel
 */
function convertToTextBox(element: ElementAttributes): PptxTextBoxModel {
  const position: PptxPositionModel = {
    left: Math.round(element.position?.left ?? 0),
    top: Math.round(element.position?.top ?? 0),
    width: Math.round(element.position?.width ?? 0),
    height: Math.round(element.position?.height ?? 0)
  };

  const fill: PptxFillModel | undefined = element.background?.color ? {
    color: element.background.color,
    opacity: element.background.opacity ?? 1.0
  } : undefined;

  const font: PptxFontModel | undefined = element.font ? {
    name: element.font.name ?? "Inter",
    size: Math.round(element.font.size ?? 16),
    font_weight: element.font.weight ?? 400,
    italic: element.font.italic ?? false,
    color: element.font.color ?? "000000"
  } : undefined;

  const paragraph: PptxParagraphModel = {
    spacing: undefined,
    alignment: convertTextAlignToPptxAlignment(element.textAlign),
    font,
    line_height: convertLineHeightToRelative(element.lineHeight, element.font?.size),
    text: element.innerText
  };

  return {
    margin: undefined,
    fill,
    position,
    text_wrap: element.textWrap ?? true,
    paragraphs: [paragraph]
  };
}

/**
 * Converts element to PptxAutoShapeBoxModel
 */
function convertToAutoShapeBox(element: ElementAttributes): PptxAutoShapeBoxModel {
  const position: PptxPositionModel = {
    left: Math.round(element.position?.left ?? 0),
    top: Math.round(element.position?.top ?? 0),
    width: Math.round(element.position?.width ?? 0),
    height: Math.round(element.position?.height ?? 0)
  };
  const fill: PptxFillModel | undefined = element.background?.color ? {
    color: element.background.color,
    opacity: element.background.opacity ?? 1.0
  } : undefined;

  const stroke: PptxStrokeModel | undefined = element.border?.color ? {
    color: element.border.color,
    thickness: element.border.width ?? 1, // float - keep as number
    opacity: element.border.opacity ?? 1.0
  } : undefined;

  const shadow: PptxShadowModel | undefined = element.shadow?.color ? {
    radius: Math.round(element.shadow.radius ?? 4), // int
    offset: Math.round(element.shadow.offset ? Math.sqrt(element.shadow.offset[0] ** 2 + element.shadow.offset[1] ** 2) : 0), // int
    color: element.shadow.color,
    opacity: element.shadow.opacity ?? 0.5, // float - keep as number
    angle: Math.round(element.shadow.angle ?? 0) // int
  } : undefined;

  // Check if element has text content
  const paragraphs: PptxParagraphModel[] | undefined = element.innerText ? [{
    spacing: undefined,
    alignment: convertTextAlignToPptxAlignment(element.textAlign),
    font: element.font ? {
      name: element.font.name ?? "Inter",
      size: Math.round(element.font.size ?? 16), // int
      font_weight: element.font.weight ?? 400,
      italic: element.font.italic ?? false,
      color: element.font.color ?? "000000"
    } : undefined,
    line_height: convertLineHeightToRelative(element.lineHeight, element.font?.size),
    text: element.innerText
  }] : undefined;

  return {
    type: PptxShapeType.ROUNDED_RECTANGLE, // Default to rounded rectangle
    margin: undefined,
    fill,
    stroke,
    shadow,
    position,
    text_wrap: element.textWrap ?? true,
    border_radius: element.borderRadius ? Math.round(element.borderRadius[0]) : 0, // int - use first value for autoshape
    paragraphs
  };
}

/**
 * Converts element to PptxPictureBoxModel
 */
function convertToPictureBox(element: ElementAttributes): PptxPictureBoxModel {
  const position: PptxPositionModel = {
    left: Math.round(element.position?.left ?? 0),
    top: Math.round(element.position?.top ?? 0),
    width: Math.round(element.position?.width ?? 0),
    height: Math.round(element.position?.height ?? 0)
  };

  const objectFit: PptxObjectFitModel = {
    fit: element.objectFit ? (element.objectFit as PptxObjectFitEnum) : PptxObjectFitEnum.CONTAIN
  };


  // Extract image path from element attributes
  const picture: PptxPictureModel = {
    is_network: element.imageSrc ? element.imageSrc.startsWith('http') : false,
    path: element.imageSrc || ''
  };

  return {
    position,
    margin: undefined,
    clip: element.clip ?? true,
    overlay: element.overlay,
    border_radius: element.borderRadius ? element.borderRadius.map(r => Math.round(r)) : undefined, // List[int] - 4 elements from route parsing
    shape: element.shape ? (element.shape as PptxBoxShapeEnum) : PptxBoxShapeEnum.RECTANGLE,
    object_fit: objectFit,
    picture
  };
}

/**
 * Converts element to PptxConnectorModel
 */
function convertToConnector(element: ElementAttributes): PptxConnectorModel {
  const position: PptxPositionModel = {
    left: Math.round(element.position?.left ?? 0),
    top: Math.round(element.position?.top ?? 0),
    width: Math.round(element.position?.width ?? 0),
    height: Math.round(element.position?.height ?? 0)
  };

  return {
    type: PptxConnectorType.STRAIGHT, // Default to straight connector
    position,
    thickness: element.border?.width ?? 0.5, // float - keep as number
    color: element.border?.color || element.background?.color || '000000',
    opacity: element.border?.opacity ?? 1.0
  };
}
