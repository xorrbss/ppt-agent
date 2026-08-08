import {
  TEMPLATE_V2_EXPORT_CANVAS,
  renderTemplateV2GeneralSlideCanvasHtml,
} from "@/lib/template-v2-general-renderer.mjs";

/**
 * The export page and the standalone HTML renderer deliberately share one
 * fail-closed implementation. Keeping one serializer prevents the browser
 * preview and the bundled presentation-export converter from drifting.
 */
export default function TemplateV2GeneralSlide({
  slide,
}: {
  slide: unknown;
}) {
  const html = renderTemplateV2GeneralSlideCanvasHtml(slide);

  return (
    <div
      className="template-v2-general-slide"
      data-template-v2-export="general"
      style={{
        position: "relative",
        width: TEMPLATE_V2_EXPORT_CANVAS.width,
        height: TEMPLATE_V2_EXPORT_CANVAS.height,
        overflow: "hidden",
        boxSizing: "border-box",
        fontFamily: "Arial, sans-serif",
      }}
      // The serializer escapes all text and attributes and rejects unknown
      // element contracts before this HTML reaches React.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
