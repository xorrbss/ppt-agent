/**
 * Stage 5 — deck-level entry points: the template import response and the
 * generated-presentation response, plus font asset normalization.
 */
import { resolveBackendAssetUrl } from "@/utils/api";
import { MAX_NUMBER_OF_SLIDES } from "@/utils/presentationLimits";
import type { Deck } from "@/components/slide-editor/types";
import {
  asRecord,
  isRecord,
  readArray,
  readNumber,
  readString,
  readValue,
  truncateString,
  type UnknownRecord,
} from "@/components/slide-editor/importing/template-v2/values";
import {
  extractTemplateV2Layouts,
  type TemplateV2Layout,
} from "@/components/slide-editor/importing/template-v2/raw-layout";
import { adaptLayoutToSlide } from "@/components/slide-editor/importing/template-v2/slide";

export type TemplateV2ImportResponse = {
  id?: unknown;
  name?: unknown;
  description?: unknown;
  icon_type?: unknown;
  merged_components?: unknown;
  raw_layouts?: unknown;
  layouts?: unknown;
  assets?: unknown;
  fonts?: unknown;
};

export type GeneratedTemplateV2PresentationResponse = {
  id?: unknown;
  title?: unknown;
  description?: unknown;
  layout?: unknown;
  slides?: unknown;
};

export function adaptTemplateV2ResponseToDeck(
  template: TemplateV2ImportResponse,
): Deck {
  const layouts = extractRenderableLayouts(template);
  if (layouts.length === 0) {
    throw new Error("Template response did not include any layouts.");
  }

  const slides = layouts
    .slice(0, MAX_NUMBER_OF_SLIDES)
    .map(adaptLayoutToSlide);
  const deck = {
    title: truncateString(readString(template.name) ?? "Imported template", 90),
    description:
      truncateString(readString(template.description) ?? "", 1200) || null,
    slides,
  } satisfies Deck;

  return deck;
}

export function normalizeTemplateV2Fonts(
  template: TemplateV2ImportResponse,
  fallbackFonts: Record<string, string> = {},
): Record<string, string> {

  const assetFonts = asRecord(template?.fonts);
  const fonts =
    assetFonts && Object.keys(assetFonts).length > 0 ? assetFonts : fallbackFonts;
  return Object.fromEntries(
    Object.entries(fonts)
      .filter(
        (entry): entry is [string, string] =>
          typeof entry[0] === "string" &&
          entry[0].trim().length > 0 &&
          typeof entry[1] === "string" &&
          entry[1].trim().length > 0,
      )
      .map(([name, url]) => [name, resolveBackendAssetUrl(url)]),
  );
}

export function adaptGeneratedTemplateV2PresentationToDeck(
  presentation: GeneratedTemplateV2PresentationResponse,
): Deck {
  const presentationRecord = asRecord(presentation) ?? {};
  const layoutPayload = readValue(presentationRecord, "layout");
  const layouts = extractTemplateV2Layouts(layoutPayload);
  const layoutById = new Map(
    layouts
      .map((layout) => [readString(layout.id), layout] as const)
      .filter((entry): entry is [string, TemplateV2Layout] => Boolean(entry[0])),
  );
  const generatedSlides = readArray(presentationRecord, "slides")
    .filter(isRecord)
    .sort((a, b) => (readNumber(a, "index") ?? 0) - (readNumber(b, "index") ?? 0));

  if (layouts.length === 0 && generatedSlides.length === 0) {
    throw new Error("Generated presentation did not include template v2 slides.");
  }

  const slides =
    generatedSlides.length > 0
      ? generatedSlides.slice(0, MAX_NUMBER_OF_SLIDES).map((slide, index) => {
        const uiLayout = readGeneratedSlideUiLayout(slide);
        if (uiLayout) {
          return adaptLayoutToSlide(uiLayout, index);
        }

        const layoutId = readString(slide.layout);
        const layout =
          (layoutId ? layoutById.get(layoutId) : null) ??
          layouts[index % layouts.length];
        if (!layout) {
          throw new Error(
            `Generated slide ${index + 1} did not include a renderable template v2 layout.`,
          );
        }
        return adaptLayoutToSlide(layout, index);
      })
      : layouts.slice(0, MAX_NUMBER_OF_SLIDES).map(adaptLayoutToSlide);

  const deck = {
    title: truncateString(
      readString(presentationRecord.title) ??
      readString(presentationRecord.id) ??
      "Generated presentation",
      90,
    ),
    description:
      truncateString(readString(presentationRecord.description) ?? "", 1200) ||
      null,
    slides,
  } satisfies Deck;

  return deck;
}

function readGeneratedSlideUiLayout(slide: UnknownRecord): TemplateV2Layout | null {
  const ui = asRecord(readValue(slide, "ui"));
  return ui ? (ui as TemplateV2Layout) : null;
}

function extractRenderableLayouts(
  template: TemplateV2ImportResponse,
): TemplateV2Layout[] {
  const layouts = extractTemplateV2Layouts(template.layouts);
  if (layouts.length > 0) return layouts;
  return extractTemplateV2Layouts(template.raw_layouts);
}
