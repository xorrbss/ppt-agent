export interface ElementAttributes {
  tagName: string;
  id?: string;
  className?: string;
  innerText?: string;
  background?: {
    color?: string;
    opacity?: number;
  };
  border?: {
    color?: string;
    width?: number;
  };
  shadow?: {
    offset?: [number, number];
    color?: string;
    opacity?: number;
  },
  font?: {
    size?: number;
    weight?: number;
    color?: string;
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
}