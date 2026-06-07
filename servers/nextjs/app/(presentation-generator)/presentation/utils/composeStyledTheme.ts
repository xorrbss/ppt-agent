import type { Theme } from "../../services/api/types";

// C2: compose a complete theme.data from a STYLE preset (its tone & manner —
// fonts + density + typography/shape/elevation/motif) plus a COLOUR set (e.g. an
// algorithmically generated brand palette from theme_generate). This lets a user
// keep a curated look while swapping in their own brand colours, without writing
// any layout code. Only keys the style actually carries are copied (additive, so
// the result stays v1-safe — deriveThemeTokens falls back for anything absent).
export function composeStyledTheme(
  style: Theme | null | undefined,
  colors: Record<string, string>
): Record<string, any> {
  const sd = (style?.data ?? {}) as Record<string, any>;
  const data: Record<string, any> = { colors, fonts: sd.fonts };
  for (const key of ["density", "typography", "shape", "elevation", "motif"]) {
    if (sd[key] != null) data[key] = sd[key];
  }
  return data;
}
