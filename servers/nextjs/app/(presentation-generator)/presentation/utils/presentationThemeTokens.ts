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

  // Tone & manner density (v2). v1 themes carry no `density` → "comfortable" =
  // the current look (unchanged). Only the spacing tokens vary by density.
  const SPACING: Record<string, { padX: number; padY: number; section: number; block: number; inline: number }> = {
    compact: { padX: 56, padY: 44, section: 22, block: 14, inline: 10 },
    comfortable: { padX: 80, padY: 64, section: 32, block: 20, inline: 12 },
    spacious: { padX: 112, padY: 84, section: 44, block: 28, inline: 16 },
  };
  const sp = SPACING[((theme?.data as any)?.density as string) ?? ""] ?? SPACING.comfortable;

  // Tone & manner typography / shape / elevation (v2 — backlog #3). All OPTIONAL
  // and ADDITIVE: a theme that omits these renders byte-identically to v1 (the
  // multipliers default to 1 and the explicit values to the current hardcoded
  // look). This is what lets curated presets feel like distinct "templates"
  // (tight editorial type + sharp/flat vs. airy rounded/elevated) without any new
  // layout code. Only the adaptive renderer reads these tokens.
  const ty = ((theme?.data as any)?.typography ?? {}) as Record<string, any>;
  const tScale = Number(ty.scale) > 0 ? Number(ty.scale) : 1;
  const fs = (rem: number) => `${Math.round(rem * tScale * 1000) / 1000}rem`;

  const sh = ((theme?.data as any)?.shape ?? {}) as Record<string, any>;
  const rScale = Number(sh.radiusScale) >= 0 ? Number(sh.radiusScale) : 1;
  const rad = (px: number) => `${Math.round(px * rScale)}px`;

  const el = ((theme?.data as any)?.elevation ?? {}) as Record<string, any>;
  const flat = el.flat === true;
  const shadow = (key: string, def: string) => el[key] ?? (flat ? "none" : def);

  const mo = ((theme?.data as any)?.motif ?? {}) as Record<string, any>;

  // Length tokens must carry a unit; a bare number (e.g. borderWidth: 2) would
  // emit unitless "2" → invalid CSS length → the property silently breaks. Coerce
  // numbers to px; strings (e.g. "2px", "-0.02em") and the default pass through.
  const len = (v: any, def: string) =>
    v == null ? def : typeof v === "number" ? `${v}px` : String(v);

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
    // typography scale (theme.data.typography; scale multiplies the ramp, default 1)
    "--fs-display": fs(3.75),
    "--fs-h1": fs(3),
    "--fs-h2": fs(2.25),
    "--fs-h3": fs(1.75),
    "--fs-h4": fs(1.375),
    "--fs-body": fs(1.125),
    "--fs-small": fs(0.95),
    "--fs-caption": fs(0.8),
    "--fw-heading": String(ty.headingWeight ?? "700"),
    "--fw-body": String(ty.bodyWeight ?? "400"),
    "--fw-emphasis": String(ty.emphasisWeight ?? "600"),
    "--lh-heading": String(ty.headingLineHeight ?? "1.15"),
    "--lh-body": String(ty.bodyLineHeight ?? "1.55"),
    "--ls-heading": len(ty.headingLetterSpacing, "-0.01em"),
    // spacing / density (driven by theme.data.density; default = comfortable)
    "--slide-pad-x": `${sp.padX}px`,
    "--slide-pad-y": `${sp.padY}px`,
    "--section-gap": `${sp.section}px`,
    "--block-gap": `${sp.block}px`,
    "--inline-gap": `${sp.inline}px`,
    // shape (theme.data.shape; radiusScale multiplies the ramp, default 1)
    "--radius-sm": rad(6),
    "--radius-md": rad(12),
    "--radius-lg": rad(20),
    "--radius-pill": "999px",
    "--border-width": len(sh.borderWidth, "1px"),
    // elevation (theme.data.elevation; flat:true drops all shadows)
    "--shadow-sm": shadow("shadowSm", "0 1px 2px rgba(0,0,0,0.04)"),
    "--shadow-md": shadow("shadowMd", "0 4px 12px rgba(0,0,0,0.06)"),
    "--shadow-lg": shadow("shadowLg", "0 12px 32px rgba(0,0,0,0.08)"),
    // motif (subtle decoration; theme.data.motif can override colour/opacity)
    "--motif-color": mo.color || accent,
    "--motif-opacity": mo.opacity != null ? String(mo.opacity) : "0.07",
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
