export const TEMPLATE_V2_RENDERED_ELEMENT_TYPES: readonly string[];
export const TEMPLATE_V2_EXPORT_CANVAS: Readonly<{
  width: number;
  height: number;
}>;

export function renderTemplateV2GeneralSlideCanvasHtml(slide: unknown): string;
export function renderTemplateV2GeneralPresentationHtml(
  presentation: unknown
): string;
