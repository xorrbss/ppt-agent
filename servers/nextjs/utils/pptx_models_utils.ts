import { ElementAttributes } from "@/types/element_attibutes";
import {
  PptxSlideModel,
  PptxTextBoxModel,
  PptxAutoShapeBoxModel,
  PptxPictureBoxModel,
  PptxConnectorModel,
  PptxPositionModel,
  PptxSpacingModel,
  PptxFillModel,
  PptxStrokeModel,
  PptxShadowModel,
  PptxFontModel,
  PptxParagraphModel,
  PptxPictureModel,
  PptxObjectFitModel,
  PptxBoxShapeEnum,
  PptxObjectFitEnum
} from "@/types/pptx_models";

/**
 * Converts ElementAttributes[][] to PptxSlideModel[]
 * Each inner array represents elements on a slide
 */
export function convertElementAttributesToPptxSlides(
  slidesAttributes: ElementAttributes[][],
  backgroundColors?: (string | undefined)[]
): PptxSlideModel[] {
  return slidesAttributes.map((slideElements, index) => {
    const shapes = slideElements.map(element => {
      return convertElementToPptxShape(element);
    }).filter(Boolean); // Remove any null/undefined shapes

    const slide: PptxSlideModel = {
      shapes: shapes as (PptxTextBoxModel | PptxAutoShapeBoxModel | PptxConnectorModel | PptxPictureBoxModel)[]
    };

    // Add background color if available
    if (backgroundColors && backgroundColors[index]) {
      slide.background = {
        color: backgroundColors[index]
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
  if (element.tagName === 'img' || element.className?.includes('image')) {
    return convertToPictureBox(element);
  }

  // Check if it's a text element
  if (element.innerText && element.innerText.trim().length > 0) {
    return convertToTextBox(element);
  }

  // Check if it's a connector/line element
  if (element.tagName === 'hr' || element.className?.includes('connector') || element.className?.includes('line')) {
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
    left: element.position?.left,
    top: element.position?.top,
    width: element.position?.width,
    height: element.position?.height
  };

  const margin: PptxSpacingModel | undefined = element.margin ? {
    top: element.margin.top,
    bottom: element.margin.bottom,
    left: element.margin.left,
    right: element.margin.right
  } : undefined;

  const fill: PptxFillModel | undefined = element.background?.color ? {
    color: element.background.color
  } : undefined;

  const font: PptxFontModel | undefined = element.font ? {
    name: element.font.name,
    size: element.font.size,
    bold: element.font.weight ? element.font.weight >= 600 : undefined,
    italic: element.font.italic,
    color: element.font.color
  } : undefined;

  const paragraph: PptxParagraphModel = {
    spacing: undefined,
    alignment: element.textAlign,
    font,
    text: element.innerText
  };

  return {
    margin,
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
    left: element.position?.left,
    top: element.position?.top,
    width: element.position?.width,
    height: element.position?.height
  };

  const margin: PptxSpacingModel | undefined = element.margin ? {
    top: element.margin.top,
    bottom: element.margin.bottom,
    left: element.margin.left,
    right: element.margin.right
  } : undefined;

  const fill: PptxFillModel | undefined = element.background?.color ? {
    color: element.background.color
  } : undefined;

  const stroke: PptxStrokeModel | undefined = element.border?.color ? {
    color: element.border.color,
    thickness: element.border.width || 1
  } : undefined;

  const shadow: PptxShadowModel | undefined = element.shadow?.color ? {
    radius: element.shadow.radius ?? 4,
    offset: element.shadow.offset ? Math.sqrt(element.shadow.offset[0] ** 2 + element.shadow.offset[1] ** 2) : undefined,
    color: element.shadow.color,
    opacity: element.shadow.opacity,
    angle: element.shadow.angle
  } : undefined;

  // Check if element has text content
  const paragraphs: PptxParagraphModel[] | undefined = element.innerText ? [{
    spacing: undefined,
    alignment: element.textAlign,
    font: element.font ? {
      name: element.font.name,
      size: element.font.size,
      bold: element.font.weight ? element.font.weight >= 600 : undefined,
      italic: element.font.italic,
      color: element.font.color
    } : undefined,
    text: element.innerText
  }] : undefined;

  return {
    margin,
    fill,
    stroke,
    shadow,
    position,
    text_wrap: element.textWrap ?? true,
    border_radius: element.borderRadius ? (Array.isArray(element.borderRadius) ? element.borderRadius[0] : element.borderRadius) : 0,
    paragraphs
  };
}

/**
 * Converts element to PptxPictureBoxModel
 */
function convertToPictureBox(element: ElementAttributes): PptxPictureBoxModel {
  const position: PptxPositionModel = {
    left: element.position?.left,
    top: element.position?.top,
    width: element.position?.width,
    height: element.position?.height
  };

  const margin: PptxSpacingModel | undefined = element.margin ? {
    top: element.margin.top,
    bottom: element.margin.bottom,
    left: element.margin.left,
    right: element.margin.right
  } : undefined;

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
    margin,
    clip: element.clip ?? false,
    overlay: element.overlay,
    border_radius: element.borderRadius ? (Array.isArray(element.borderRadius) ? element.borderRadius : [element.borderRadius]) : undefined,
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
    left: element.position?.left,
    top: element.position?.top,
    width: element.position?.width,
    height: element.position?.height
  };

  return {
    type: element.connectorType,
    position,
    thickness: element.border?.width || 1,
    color: element.border?.color || element.background?.color || '#000000'
  };
}
