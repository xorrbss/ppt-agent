import type { DeckMasterContractV1, SemanticStyle } from "./contracts.ts";

export const EDITABLE_SEMANTIC_STYLES = [
  "eyebrow",
  "title",
  "note",
  "rightCallout",
] as const;

export type EditableSemanticStyle = (typeof EDITABLE_SEMANTIC_STYLES)[number];
type ColorToken = keyof DeckMasterContractV1["tokens"]["colors"];

function boundedNumber(value: number, fallback: number): number {
  return Number.isFinite(value) ? Math.max(1, Math.min(200, value)) : fallback;
}

export function setMasterColor(
  contract: DeckMasterContractV1,
  token: ColorToken,
  color: string,
): DeckMasterContractV1 {
  return {
    ...contract,
    tokens: {
      ...contract.tokens,
      colors: { ...contract.tokens.colors, [token]: color },
    },
  };
}

export function setMasterPrimaryFont(
  contract: DeckMasterContractV1,
  primary: string,
): DeckMasterContractV1 {
  return {
    ...contract,
    fontManifest: {
      ...contract.fontManifest,
      primary,
      windowsFallback: primary,
      themeScripts: {
        latin: primary,
        hang: primary,
        jpan: primary,
        hans: primary,
        hant: primary,
      },
    },
    tokens: {
      ...contract.tokens,
      fonts: {
        ...contract.tokens.fonts,
        latin: primary,
        eastAsian: primary,
      },
    },
  };
}

export function setMasterFallbackFonts(
  contract: DeckMasterContractV1,
  rawFallbacks: string,
): DeckMasterContractV1 {
  const fallbacks = rawFallbacks
    .split(",")
    .map((font) => font.trim())
    .filter(Boolean);
  return {
    ...contract,
    fontManifest: { ...contract.fontManifest, fallbacks },
    tokens: {
      ...contract.tokens,
      fonts: { ...contract.tokens.fonts, fallbacks },
    },
  };
}

export function setMasterFurnitureText(
  contract: DeckMasterContractV1,
  furniture: "header" | "pageNumber",
  text: string,
): DeckMasterContractV1 {
  return {
    ...contract,
    furniture: {
      ...contract.furniture,
      [furniture]: { ...contract.furniture[furniture], text },
    },
  };
}

export function setMasterFurnitureColor(
  contract: DeckMasterContractV1,
  furniture: "divider" | "pageNumber",
  color: string,
): DeckMasterContractV1 {
  const field = furniture === "divider" ? "stroke" : "fill";
  return {
    ...contract,
    furniture: {
      ...contract.furniture,
      [furniture]: { ...contract.furniture[furniture], [field]: color },
    },
  };
}

export function setMasterSemanticFontPt(
  contract: DeckMasterContractV1,
  style: EditableSemanticStyle,
  fontPt: number,
): DeckMasterContractV1 {
  const current = contract.semanticStyles[style];
  return {
    ...contract,
    semanticStyles: {
      ...contract.semanticStyles,
      [style]: { ...current, fontPt: boundedNumber(fontPt, current.fontPt) },
    },
  };
}

export function setMasterTitleMinimumFontPt(
  contract: DeckMasterContractV1,
  minFontPt: number,
): DeckMasterContractV1 {
  const title: SemanticStyle = contract.semanticStyles.title;
  return {
    ...contract,
    semanticStyles: {
      ...contract.semanticStyles,
      title: {
        ...title,
        minFontPt: Math.min(
          title.fontPt,
          boundedNumber(minFontPt, title.minFontPt),
        ),
      },
    },
  };
}
