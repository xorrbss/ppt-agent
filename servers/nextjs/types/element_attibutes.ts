export interface ElementAttributes {
  tagName: string;
  id?: string;
  className?: string;
  innerText?: string;
  background?: {
    color?: string;
    opacity?: number;
    isInherited?: boolean;
  };
  border?: {
    color?: string;
    width?: number;
  };
  shadow?: {
    offset?: [number, number];
    color?: string;
    opacity?: number;
    radius?: number;
    angle?: number;
    spread?: number;
    inset?: boolean;
  },
  font?: {
    name?: string;
    size?: number;
    weight?: number;
    color?: string;
    italic?: boolean;
  };
  position?: {
    left?: number;
    top?: number;
    width?: number;
    height?: number;
  };
  margin?: {
    top?: number;
    bottom?: number;
    left?: number;
    right?: number;
  };
  padding?: {
    top?: number;
    bottom?: number;
    left?: number;
    right?: number;
  };
  zIndex?: number;
  textAlign?: 'left' | 'center' | 'right' | 'justify';
  borderRadius?: number[];
  imageSrc?: string;
  objectFit?: 'contain' | 'cover' | 'fill';
  clip?: boolean;
  overlay?: string;
  shape?: 'rectangle' | 'circle';
  connectorType?: string;
  textWrap?: boolean;
}

export interface SlideAttributesResult {
  elements: ElementAttributes[];
  backgroundColor?: string;
}