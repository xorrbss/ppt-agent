import { getApiUrl } from "@/utils/api";

import { ApiResponseHandler } from "./api-error-handler";
import { isRecord } from "@/lib/guards";
import { getHeader } from "./header";
import {
  normalizeAuthoredStyleSamples,
  type AuthoredStyleSamples,
} from "./authored-style-samples";
import {
  normalizeSupportedCanvases,
  type AuthoredStyleSupportedCanvas,
} from "./authored-supported-canvases";

export interface AuthoredStylePreview {
  bg: string;
  accent: string;
  palette: string[];
  variant: string;
  image?: string;
}

export const AUTHORED_STYLE_CATEGORIES = [
  "general",
  "business",
  "technology",
  "research",
  "editorial",
  "creative",
] as const;

export type AuthoredStyleCategory = (typeof AUTHORED_STYLE_CATEGORIES)[number];

export type {
  AuthoredCanvasDisabledReason,
  AuthoredCanvasRowId,
  AuthoredStyleSupportedCanvas,
} from "./authored-supported-canvases";

export interface AuthoredStyleSummary {
  id: string;
  name: string;
  description: string;
  category: AuthoredStyleCategory;
  tags: string[];
  use_cases: string[];
  preview: AuthoredStylePreview;
  aliases?: string[];
  supportedAspectRatios: Array<"16:9" | "9:16" | "9:13" | "1:1" | "21:9">;
  /** Preferred over `supportedAspectRatios` when present (canvas-expansion rollout). */
  supportedCanvases?: AuthoredStyleSupportedCanvas[];
  samples?: AuthoredStyleSamples;
}

export const DEFAULT_AUTHORED_STYLE: AuthoredStyleSummary = {
  id: "default",
  name: "기본 블루프린트",
  description: "깔끔한 흰 바탕과 브랜드 블루로 구성한 범용 프레젠테이션 스타일",
  category: "general",
  tags: ["범용", "깔끔한", "컨설팅"],
  use_cases: ["회사 소개", "프로젝트 제안", "일반 보고서"],
  supportedAspectRatios: ["16:9"],
  preview: {
    bg: "#F8FAFC",
    accent: "#2563EB",
    palette: ["#F8FAFC", "#FFFFFF", "#0F172A", "#64748B", "#2563EB"],
    variant: "clean-light",
  },
};

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  const seen = new Set<string>();
  return value.flatMap((item) => {
    if (typeof item !== "string") return [];
    const normalized = item.trim();
    if (!normalized || seen.has(normalized)) return [];
    seen.add(normalized);
    return [normalized];
  });
}

function normalizeCategory(value: unknown): AuthoredStyleCategory {
  return typeof value === "string" &&
    AUTHORED_STYLE_CATEGORIES.includes(value as AuthoredStyleCategory)
    ? (value as AuthoredStyleCategory)
    : "general";
}

function toAuthoredStyleSummary(value: unknown): AuthoredStyleSummary | null {
  if (!isRecord(value) || !isRecord(value.preview)) return null;

  const { id, name, description, preview } = value;
  if (
    typeof id !== "string" ||
    !id.trim() ||
    typeof name !== "string" ||
    !name.trim() ||
    typeof description !== "string" ||
    typeof preview.bg !== "string" ||
    !preview.bg.trim() ||
    typeof preview.accent !== "string" ||
    !preview.accent.trim()
  ) {
    return null;
  }

  const previewShape: AuthoredStylePreview = {
    bg: preview.bg.trim(),
    accent: preview.accent.trim(),
    palette: (() => {
      const palette = normalizeStringList(preview.palette);
      return palette.length > 0
        ? palette
        : normalizeStringList([preview.bg, preview.accent]);
    })(),
    variant:
      typeof preview.variant === "string" && preview.variant.trim()
        ? preview.variant.trim()
        : "clean-light",
  };
  // Only attach `image` when the backend supplied one — emitting `image: undefined`
  // would leak an own property that breaks deep-equality against the public shape.
  if (typeof preview.image === "string" && preview.image.trim()) {
    previewShape.image = getApiUrl(preview.image.trim());
  }

  const samples = normalizeAuthoredStyleSamples(value.samples);
  const supportedCanvases = normalizeSupportedCanvases(value.supportedCanvases);

  // Rebuild the public shape so internal fields such as `brief` never reach UI state.
  return {
    id: id.trim(),
    name: name.trim(),
    description: description.trim(),
    category: normalizeCategory(value.category),
    tags: normalizeStringList(value.tags),
    use_cases: normalizeStringList(value.use_cases),
    supportedAspectRatios: Array.isArray(value.supportedAspectRatios)
      ? value.supportedAspectRatios.filter(
          (ratio): ratio is "16:9" | "9:16" | "9:13" | "1:1" | "21:9" =>
            ratio === "16:9" || ratio === "9:16" || ratio === "9:13" || ratio === "1:1" || ratio === "21:9",
        )
      : ["16:9"],
    preview: previewShape,
    ...(Array.isArray(value.aliases)
      ? { aliases: normalizeStringList(value.aliases) }
      : {}),
    // Same rule as `preview.image`: no own `samples` property unless valid.
    ...(samples ? { samples } : {}),
    ...(supportedCanvases ? { supportedCanvases } : {}),
  };
}

import { normalizeDocumentCanvasCapabilities, unavailableDocumentCanvasCapabilities, type DocumentCanvasCapabilities, type DocumentCanvasSelection } from "./document-canvas-capabilities";
export type { DocumentCanvasCapabilities, DocumentCanvasSelection } from "./document-canvas-capabilities";

export function canonicalAuthoredStyleId(
  styles: AuthoredStyleSummary[],
  styleId: string,
): string {
  if (styles.some((style) => style.id === styleId)) return styleId;
  return styles.find((style) => style.aliases?.includes(styleId))?.id ?? styleId;
}

export function normalizeAuthoredStyles(
  payload: unknown,
): AuthoredStyleSummary[] {
  const uniqueStyles: AuthoredStyleSummary[] = [];
  const seenIds = new Set<string>();

  if (Array.isArray(payload)) {
    for (const value of payload) {
      const style = toAuthoredStyleSummary(value);
      if (!style || seenIds.has(style.id)) continue;
      seenIds.add(style.id);
      uniqueStyles.push(style);
    }
  }

  const serverDefault = uniqueStyles.find(
    (style) => style.id === DEFAULT_AUTHORED_STYLE.id,
  );
  const remainingStyles = uniqueStyles.filter(
    (style) => style.id !== DEFAULT_AUTHORED_STYLE.id,
  );

  return [serverDefault ?? DEFAULT_AUTHORED_STYLE, ...remainingStyles];
}

class AuthoredStylesApi {
  static async getStyles(): Promise<AuthoredStyleSummary[]> {
    try {
      const response = await fetch(getApiUrl("/api/v1/ppt/authored/styles"), {
        method: "GET",
        headers: getHeader(),
        cache: "no-store",
      });
      const payload: unknown = await ApiResponseHandler.handleResponse(
        response,
        "Failed to get authored styles",
      );
      return normalizeAuthoredStyles(payload);
    } catch (error) {
      console.error("Error getting authored styles:", error);
      throw error;
    }
  }

  /** The server alone decides which selected route is ready for A4. */
  static async getDocumentCanvasCapabilities(selection?: DocumentCanvasSelection): Promise<DocumentCanvasCapabilities> {
    try {
      const query = selection ? `?${new URLSearchParams({
        generation_strategy: selection.generationStrategy,
        authored_style: selection.authoredStyle,
        source_content_mode: String(selection.sourceContentMode),
        has_company_standard: String(selection.hasCompanyStandard),
      }).toString()}` : "";
      const response = await fetch(
        getApiUrl(`/api/v1/ppt/authored/canvas-capabilities${query}`),
        { method: "GET", headers: getHeader(), cache: "no-store" },
      );
      const payload: unknown = await ApiResponseHandler.handleResponse(
        response,
        "Failed to get document canvas capabilities",
      );
      return normalizeDocumentCanvasCapabilities(payload);
    } catch {
      return unavailableDocumentCanvasCapabilities();
    }
  }
}

export default AuthoredStylesApi;
