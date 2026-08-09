import { resolveBackendAssetUrl } from "@/utils/api";
import {
  escapeAttribute,
  escapeCssFont,
  escapeCssUrl,
  escapeStyleText,
} from "./css";
import type { FontFaceDefinition } from "./types";
import {
  readArray,
  readRecord,
  readString,
  readStringValueOrNull,
} from "./values";

export function renderFontAssetTags(fonts: unknown): string {
  const css = readStringValueOrNull(fonts);
  if (css) {
    return `<style>${escapeStyleText(css)}${fontCssFamilyAliases(css)}</style>`;
  }

  const tags: string[] = [];
  const records = readRecord(fonts);
  const embeddedCss = readStringValueOrNull(records.css ?? records.font_css);
  if (embeddedCss) {
    tags.push(
      `<style>${escapeStyleText(embeddedCss)}${fontCssFamilyAliases(embeddedCss)}</style>`
    );
  }

  const faceEntries = readArray(records.fonts).length ? records.fonts : fonts;
  tags.push(...normalizeFontFaces(faceEntries).map(renderFontFaceDefinition));

  const stylesheets = normalizeFontStylesheetUrls(faceEntries);
  tags.push(
    ...stylesheets.map(
      (url) =>
        `<link rel="stylesheet" href="${escapeAttribute(resolveBackendAssetUrl(url))}">`
    )
  );

  return tags.join("");
}

function normalizeFontFaces(fonts: unknown): FontFaceDefinition[] {
  if (Array.isArray(fonts)) {
    return fonts.flatMap((entry) => normalizeFontFaceEntry(undefined, entry));
  }

  return Object.entries(readRecord(fonts)).flatMap(([family, value]) =>
    family === "css" || family === "font_css" || family === "fonts"
      ? []
      : normalizeFontFaceEntry(family, value)
  );
}

function normalizeFontFaceEntry(
  fallbackFamily: string | undefined,
  value: unknown
): FontFaceDefinition[] {
  if (Array.isArray(value)) {
    return value.flatMap((entry) => normalizeFontFaceEntry(fallbackFamily, entry));
  }

  const url = readString(value);
  if (url) {
    if (isFontStylesheetUrl(url)) return [];
    const family = fallbackFamily?.trim();
    return family
      ? [
        {
          family,
          url,
          weight: inferFontWeight(`${family} ${url}`),
          style: inferFontStyle(`${family} ${url}`),
        },
      ]
      : [];
  }

  const record = readRecord(value);
  const family = readString(
    record.family ?? record.name ?? record.fontFamily ?? record.font_family
  ) ?? fallbackFamily?.trim();
  const source = readString(
    record.url ?? record.src ?? record.href ?? record.data ?? record.source
  );
  if (!family || !source || isFontStylesheetUrl(source)) return [];

  return [
    {
      family,
      url: source,
      format: readString(record.format) ?? undefined,
      weight:
        readFontWeight(record.weight ?? record.fontWeight ?? record.font_weight) ??
        inferFontWeight(`${family} ${source}`),
      style:
        readFontStyle(record.style ?? record.fontStyle ?? record.font_style) ??
        inferFontStyle(`${family} ${source}`),
    },
  ];
}

function normalizeFontStylesheetUrls(fonts: unknown): string[] {
  if (Array.isArray(fonts)) {
    return fonts.flatMap(normalizeFontStylesheetUrls);
  }

  const directUrl = readString(fonts);
  if (directUrl && isFontStylesheetUrl(directUrl)) return [directUrl];

  return Object.values(readRecord(fonts)).flatMap((value) => {
    const url = readString(value);
    if (url && isFontStylesheetUrl(url)) return [url];

    const record = readRecord(value);
    const source = readString(
      record.url ?? record.src ?? record.href ?? record.data ?? record.source
    );
    return source && isFontStylesheetUrl(source) ? [source] : [];
  });
}

function renderFontFaceDefinition(definition: FontFaceDefinition): string {
  const aliases = fontFamilyAliases(definition.family, definition.weight);
  const src = `url("${escapeCssUrl(resolveBackendAssetUrl(definition.url))}")${definition.format ? ` format("${escapeCssUrl(definition.format)}")` : ""
    }`;
  return aliases
    .map(
      (family) =>
        `<style>@font-face{font-family:${escapeCssFont(
          family
        )};src:${src};font-weight:${definition.weight ?? "400"};font-style:${definition.style ?? "normal"
        };font-display:swap}</style>`
    )
    .join("");
}

function fontCssFamilyAliases(css: string): string {
  const aliases: string[] = [];
  const facePattern = /@font-face\s*\{[^}]*font-family\s*:\s*(['"]?)([^;'"}]+)\1[^}]*\}/gi;
  for (const match of css.matchAll(facePattern)) {
    const block = match[0];
    const family = match[2]?.trim();
    if (!family) continue;
    const weight = /font-weight\s*:\s*([^;}]+)/i.exec(block)?.[1]?.trim();
    for (const alias of fontFamilyAliases(family, weight).filter(
      (item) => item !== family
    )) {
      aliases.push(
        block.replace(
          /font-family\s*:\s*(['"]?)([^;'"}]+)\1/i,
          `font-family:${escapeCssFont(alias)}`
        )
      );
    }
  }
  return aliases.join("");
}

function fontFamilyAliases(family: string, weight?: string): string[] {
  const normalized = family.trim();
  const aliases = new Set([normalized]);
  const alias = normalized
    .replace(/\s+(regular|bold\s*italic|bold|italic|black|semibold|semi\s*bold|medium|light)$/i, "")
    .trim();

  if (alias && alias !== normalized && (weight || inferFontWeight(normalized))) {
    aliases.add(alias);
  }

  return [...aliases];
}

function readFontWeight(value: unknown): string | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(Math.round(value));
  }
  const text = readString(value);
  if (!text) return undefined;
  const normalized = text.toLowerCase().replace(/\s+/g, "");
  const namedWeights: Record<string, string> = {
    thin: "100",
    extralight: "200",
    ultralight: "200",
    light: "300",
    regular: "400",
    normal: "400",
    medium: "500",
    semibold: "600",
    demibold: "600",
    bold: "700",
    extrabold: "800",
    ultrabold: "800",
    black: "900",
    heavy: "900",
  };
  if (namedWeights[normalized]) return namedWeights[normalized];
  return /^\d{3}$/.test(normalized) ? normalized : undefined;
}

function inferFontWeight(value: string): string | undefined {
  const normalized = decodeFontHint(value).toLowerCase();
  if (/\b(black|heavy)\b/.test(normalized)) return "900";
  if (/\b(extra|ultra)[\s_-]?bold\b/.test(normalized)) return "800";
  if (/\bbold\b/.test(normalized)) return "700";
  if (/\b(semi|demi)[\s_-]?bold\b/.test(normalized)) return "600";
  if (/\bmedium\b/.test(normalized)) return "500";
  if (/\bregular\b|\bnormal\b/.test(normalized)) return "400";
  if (/\blight\b/.test(normalized)) return "300";
  if (/\b(extra|ultra)[\s_-]?light\b/.test(normalized)) return "200";
  if (/\bthin\b/.test(normalized)) return "100";
  return undefined;
}

function readFontStyle(value: unknown): string | undefined {
  const text = readString(value)?.toLowerCase();
  return text === "italic" || text === "oblique" || text === "normal"
    ? text
    : undefined;
}

function inferFontStyle(value: string): string | undefined {
  return /\bitalic\b/i.test(decodeFontHint(value)) ? "italic" : undefined;
}

function decodeFontHint(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function isFontStylesheetUrl(url: string): boolean {
  return /\.css(\?|$)/i.test(url) || /fonts\.googleapis\.com/.test(url);
}
