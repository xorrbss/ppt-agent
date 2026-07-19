import { getApiUrl } from "@/utils/api";

import { ApiResponseHandler } from "./api-error-handler";
import { getHeader } from "./header";

export interface AuthoredStylePreview {
  bg: string;
  accent: string;
}

export interface AuthoredStyleSummary {
  id: string;
  name: string;
  description: string;
  preview: AuthoredStylePreview;
}

export const DEFAULT_AUTHORED_STYLE: AuthoredStyleSummary = {
  id: "default",
  name: "기본 블루프린트",
  description:
    "깔끔한 흰 바탕과 브랜드 블루로 구성한 범용 프레젠테이션 스타일",
  preview: {
    bg: "#F8FAFC",
    accent: "#2563EB",
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toAuthoredStyleSummary(value: unknown): AuthoredStyleSummary | null {
  if (!isRecord(value) || !isRecord(value.preview)) return null;

  const { id, name, description, preview } = value;
  if (
    typeof id !== "string" ||
    !id ||
    typeof name !== "string" ||
    !name ||
    typeof description !== "string" ||
    typeof preview.bg !== "string" ||
    !preview.bg ||
    typeof preview.accent !== "string" ||
    !preview.accent
  ) {
    return null;
  }

  // Rebuild the public shape so internal fields such as `brief` never reach UI state.
  return {
    id,
    name,
    description,
    preview: {
      bg: preview.bg,
      accent: preview.accent,
    },
  };
}

export function normalizeAuthoredStyles(payload: unknown): AuthoredStyleSummary[] {
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
    (style) => style.id === DEFAULT_AUTHORED_STYLE.id
  );
  const remainingStyles = uniqueStyles.filter(
    (style) => style.id !== DEFAULT_AUTHORED_STYLE.id
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
        "Failed to get authored styles"
      );
      return normalizeAuthoredStyles(payload);
    } catch (error) {
      console.error("Error getting authored styles:", error);
      throw error;
    }
  }
}

export default AuthoredStylesApi;
