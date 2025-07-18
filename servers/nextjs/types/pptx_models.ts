export enum PptxBoxShapeEnum {
  RECTANGLE = "rectangle",
  CIRCLE = "circle"
}

export enum PptxObjectFitEnum {
  CONTAIN = "contain",
  COVER = "cover",
  FILL = "fill"
}

export interface PptxSpacingModel {
  top?: number;
  bottom?: number;
  left?: number;
  right?: number;
}

export interface PptxPositionModel {
  left?: number;
  top?: number;
  width?: number;
  height?: number;
}

export interface PptxFontModel {
  name?: string;
  size?: number;
  bold?: boolean;
  italic?: boolean;
  color?: string;
}

export interface PptxFillModel {
  color: string;
}

export interface PptxStrokeModel {
  color: string;
  thickness: number;
}

export interface PptxShadowModel {
  radius: number;
  offset?: number;
  color?: string;
  opacity?: number;
  angle?: number;
}

export interface PptxTextRunModel {
  text: string;
  font?: PptxFontModel;
}

export interface PptxParagraphModel {
  spacing?: PptxSpacingModel;
  alignment?: any;
  font?: PptxFontModel;
  text?: string;
  text_runs?: PptxTextRunModel[];
}

export interface PptxObjectFitModel {
  fit?: PptxObjectFitEnum;
  focus?: [number | null, number | null];
}

export interface PptxPictureModel {
  is_network: boolean;
  path: string;
}

export interface PptxShapeModel {
}

export interface PptxTextBoxModel extends PptxShapeModel {
  margin?: PptxSpacingModel;
  fill?: PptxFillModel;
  position: PptxPositionModel;
  text_wrap?: boolean;
  paragraphs: PptxParagraphModel[];
}

export interface PptxAutoShapeBoxModel extends PptxShapeModel {
  type?: any;
  margin?: PptxSpacingModel;
  fill?: PptxFillModel;
  stroke?: PptxStrokeModel;
  shadow?: PptxShadowModel;
  position: PptxPositionModel;
  text_wrap?: boolean;
  border_radius?: number;
  paragraphs?: PptxParagraphModel[];
}

export interface PptxPictureBoxModel extends PptxShapeModel {
  position: PptxPositionModel;
  margin?: PptxSpacingModel;
  clip?: boolean;
  overlay?: string;
  border_radius?: number[];
  shape?: PptxBoxShapeEnum;
  object_fit?: PptxObjectFitModel;
  picture: PptxPictureModel;
}

export interface PptxConnectorModel extends PptxShapeModel {
  type?: any;
  position: PptxPositionModel;
  thickness?: number;
  color?: string;
}

export interface PptxSlideModel {
  shapes: (PptxTextBoxModel | PptxAutoShapeBoxModel | PptxConnectorModel | PptxPictureBoxModel)[];
}

export interface PptxPresentationModel {
  background_color: string;
  shapes?: PptxShapeModel[];
  slides: PptxSlideModel[];
}

export const createPptxSpacingAll = (num: number): PptxSpacingModel => ({
  top: num,
  left: num,
  bottom: num,
  right: num
});

export const createPptxPositionForTextbox = (left: number, top: number, width: number): PptxPositionModel => ({
  left,
  top,
  width,
  height: 100
});

export const positionToPtList = (position: PptxPositionModel): number[] => {
  return [position.left || 0, position.top || 0, position.width || 0, position.height || 0];
};

export const positionToPtXyxy = (position: PptxPositionModel): number[] => {
  const left = position.left || 0;
  const top = position.top || 0;
  const width = position.width || 0;
  const height = position.height || 0;
  
  return [left, top, left + width, top + height];
};
