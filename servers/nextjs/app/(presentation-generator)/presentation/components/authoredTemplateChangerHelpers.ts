import type { AuthoredStyleCategory } from "../../services/api/authored";

export const CATEGORY_LABELS: Record<AuthoredStyleCategory, string> = {
  general: "범용",
  business: "비즈니스",
  technology: "기술",
  research: "리서치",
  editorial: "에디토리얼",
  creative: "크리에이티브",
};

export function normalizeSearchText(value: string) {
  return value.normalize("NFKC").trim().toLocaleLowerCase("ko-KR");
}

export function createIdempotencyKey(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function targetPresentationUrl(presentationId: string): string {
  return `/presentation?id=${encodeURIComponent(presentationId)}&workspace_staging=template`;
}
