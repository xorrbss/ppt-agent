import { DEFAULT_THEMES } from "@/app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/constants";
import ThemeApi from "@/app/(presentation-generator)/services/api/theme";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";

// Apply a chosen theme (a built-in preset id, or a saved custom theme id) to a
// freshly-created deck. Best-effort: theme is presentation-level and applied at
// render time, so a PATCH after create is enough for the editor/export to render
// with it. No selection (or unresolvable id) → no-op.
export async function applyDeckTheme(
  presentationId: string,
  selectedThemeId: string | null
): Promise<void> {
  if (!selectedThemeId) return;
  try {
    let theme: any = DEFAULT_THEMES.find((t: any) => t.id === selectedThemeId);
    if (!theme) {
      const customs = await ThemeApi.getThemes();
      theme = (customs || []).find((t: any) => t.id === selectedThemeId);
    }
    if (!theme) return;
    await PresentationGenerationApi.updatePresentationContent({ id: presentationId, theme });
  } catch (error) {
    console.error("Failed to apply selected theme", error);
  }
}
