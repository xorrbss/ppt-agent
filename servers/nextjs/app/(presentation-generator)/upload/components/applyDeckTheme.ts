import { DEFAULT_THEMES } from "@/app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/constants";
import ThemeApi from "@/app/(presentation-generator)/services/api/theme";
import {
  PresentationGenerationApi,
  PresentationThemePayload,
} from "../../services/api/presentation-generation";

const normalizeTheme = (value: unknown): PresentationThemePayload | null => {
  if (!value || typeof value !== "object") return null;
  const theme = value as Record<string, unknown>;
  if (
    typeof theme.id !== "string" ||
    typeof theme.name !== "string" ||
    typeof theme.description !== "string" ||
    !theme.data ||
    typeof theme.data !== "object" ||
    Array.isArray(theme.data)
  ) {
    return null;
  }

  const optionalString = (field: string): string | null =>
    typeof theme[field] === "string" ? theme[field] : null;

  // Keep the create admission payload identical to the presentation theme
  // shape. In particular, do not forward arbitrary properties returned by a
  // custom theme service.
  return {
    id: theme.id,
    name: theme.name,
    description: theme.description,
    user: typeof theme.user === "string" ? theme.user : "builtin",
    logo: optionalString("logo"),
    logo_url: optionalString("logo_url"),
    company_name: optionalString("company_name"),
    data: theme.data as Record<string, unknown>,
  };
};

export async function resolveDeckTheme(
  selectedThemeId: string | null
): Promise<PresentationThemePayload | null> {
  if (!selectedThemeId) return null;
  try {
    const builtInTheme = DEFAULT_THEMES.find(
      (theme: { id?: string }) => theme.id === selectedThemeId
    );
    if (builtInTheme) return normalizeTheme(builtInTheme);

    const customThemes = await ThemeApi.getThemes();
    return normalizeTheme(
      (customThemes || []).find((theme) => theme.id === selectedThemeId)
    );
  } catch (error) {
    console.error("Failed to resolve selected theme", error);
    return null;
  }
}

// Apply a chosen theme (a built-in preset id, or a saved custom theme id) to a
// freshly-created deck. Best-effort: theme is presentation-level and applied at
// render time, so a PATCH after create is enough for the editor/export to render
// with it. No selection (or unresolvable id) → no-op.
export async function applyDeckTheme(
  presentationId: string,
  selectedThemeId: string | null
): Promise<void> {
  try {
    const theme = await resolveDeckTheme(selectedThemeId);
    if (!theme) return;
    await PresentationGenerationApi.updatePresentationContent({
      id: presentationId,
      theme,
    });
  } catch (error) {
    console.error("Failed to apply selected theme", error);
  }
}
