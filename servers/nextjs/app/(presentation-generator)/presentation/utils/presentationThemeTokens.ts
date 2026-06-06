import type { Theme } from "../../services/api/types";

// Phase 2 — tone & manner design tokens.
// Derived ADDITIVELY from the existing 16-colour theme so legacy templates
// (which read only the original --primary-color/etc.) are unaffected. Only the
// adaptive renderer consumes these extended tokens.

export function deriveThemeTokens(theme: Theme | null | undefined): Record<string, string> {
  const c = (theme?.data?.colors ?? {}) as Record<string, string>;
  const primary = c["primary"] || "#2563eb";
  const bg = c["background"] || "#ffffff";
  const bgText = c["background_text"] || "#111827";
  const card = c["card"] || "#f8fafc";
  const stroke = c["stroke"] || "#e5e7eb";
  const secondary = c["graph_1"] || primary;
  const accent = c["graph_2"] || c["graph_1"] || primary;

  return {
    // extended colour roles (light/dark-adaptive via color-mix)
    "--secondary-color": secondary,
    "--accent-color": accent,
    "--surface-color": card,
    "--surface-variant": `color-mix(in srgb, ${card} 70%, ${bg})`,
    "--on-surface": bgText,
    "--muted-color": `color-mix(in srgb, ${bgText} 58%, ${bg})`,
    "--border-color": stroke,
    "--success": "#16a34a",
    "--warning": "#d97706",
    "--danger": "#dc2626",
    "--info": primary,
    // typography scale (defaults match the current adaptive look; theme can vary later)
    "--fs-display": "3.75rem",
    "--fs-h1": "3rem",
    "--fs-h2": "2.25rem",
    "--fs-h3": "1.75rem",
    "--fs-h4": "1.375rem",
    "--fs-body": "1.125rem",
    "--fs-small": "0.95rem",
    "--fs-caption": "0.8rem",
    "--fw-heading": "700",
    "--fw-body": "400",
    "--fw-emphasis": "600",
    "--lh-heading": "1.15",
    "--lh-body": "1.55",
    "--ls-heading": "-0.01em",
    // spacing / density
    "--slide-pad-x": "80px",
    "--slide-pad-y": "64px",
    "--section-gap": "32px",
    "--block-gap": "20px",
    "--inline-gap": "12px",
    // shape
    "--radius-sm": "6px",
    "--radius-md": "12px",
    "--radius-lg": "20px",
    "--radius-pill": "999px",
    "--border-width": "1px",
    "--shadow-sm": "0 1px 2px rgba(0,0,0,0.04)",
    "--shadow-md": "0 4px 12px rgba(0,0,0,0.06)",
    "--shadow-lg": "0 12px 32px rgba(0,0,0,0.08)",
    // motif (subtle decoration)
    "--motif-color": accent,
    "--motif-opacity": "0.07",
  };
}

/** Set the extended tone & manner tokens on a theme host element (additive). */
export function applyPresentationThemeTokens(
  element: HTMLElement | null,
  theme: Theme | null | undefined
): void {
  if (!element || !theme?.data?.colors) return;
  const tokens = deriveThemeTokens(theme);
  for (const [key, value] of Object.entries(tokens)) {
    if (value) element.style.setProperty(key, value);
  }
}
