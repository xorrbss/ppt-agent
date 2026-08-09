/**
 * Template v2 → editor deck import. The implementation is split by pipeline
 * stage under `./template-v2/`; this module is the stable public entry point.
 *
 *   values        — total-function readers/coercers over raw JSON
 *   raw-frames    — parse: geometry over raw payload records
 *   raw-layout    — parse: locating layouts/components, flow child sizing
 *   element-base  — map: frame/identity fields shared by every element
 *   styles        — map: font, fill, stroke, radius, padding, shadow
 *   content       — map: text runs, list items, table cells, chart series
 *   element-bounds— map: bounds of adapted elements
 *   elements      — map: element dispatcher + box-model elements
 *   graphics-elements — map: vector, chart, infographic
 *   normalize     — normalize: post-mapping passes (author info card)
 *   slide         — assembling elements into a slide
 *   deck          — deck-level entry points and font assets
 */
export type { TemplateV2Layout } from "@/components/slide-editor/importing/template-v2/raw-layout";
export {
  extractTemplateV2Layouts,
  extractTemplateV2MergedComponents,
  withEqualTemplateV2FlowChildSizes,
} from "@/components/slide-editor/importing/template-v2/raw-layout";

export { normalizeTemplateV2Slide } from "@/components/slide-editor/importing/template-v2/normalize";

export {
  adaptTemplateV2ComponentToElement,
  adaptTemplateV2LayoutToSlide,
} from "@/components/slide-editor/importing/template-v2/slide";

export type {
  GeneratedTemplateV2PresentationResponse,
  TemplateV2ImportResponse,
} from "@/components/slide-editor/importing/template-v2/deck";
export {
  adaptGeneratedTemplateV2PresentationToDeck,
  adaptTemplateV2ResponseToDeck,
  normalizeTemplateV2Fonts,
} from "@/components/slide-editor/importing/template-v2/deck";
