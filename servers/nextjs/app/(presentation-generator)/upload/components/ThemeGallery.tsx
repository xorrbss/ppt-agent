import React from "react";
import { DEFAULT_THEMES } from "@/app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/constants";

// Theme-preset gallery for the new-deck flow. Picking a preset stores its id; the
// upload handler PATCHes the full preset onto the created deck so it renders with
// that look. Each swatch reflects colour + shape (radius) + elevation (shadow) —
// the dimensions that distinguish the v2 presets — without loading every font.

const swatchRadius = (theme: any): string =>
  `${Math.round(14 * (theme?.data?.shape?.radiusScale ?? 1))}px`;

const swatchShadow = (theme: any): string => {
  const el = theme?.data?.elevation;
  if (el?.flat) return "none";
  return el?.shadowMd ?? "0 4px 12px rgba(0,0,0,0.12)";
};

const Swatch: React.FC<{ theme: any }> = ({ theme }) => {
  const c = theme.data.colors;
  return (
    <div
      className="h-[68px] w-full overflow-hidden rounded-lg p-2.5"
      style={{ background: c.background }}
    >
      <div
        className="h-full w-full p-2"
        style={{
          background: c.card,
          border: `1px solid ${c.stroke}`,
          borderRadius: swatchRadius(theme),
          boxShadow: swatchShadow(theme),
        }}
      >
        <div className="h-2 w-3/5 rounded-full" style={{ background: c.primary }} />
        <div className="mt-2 h-1.5 w-full rounded-full" style={{ background: c.background_text, opacity: 0.65 }} />
        <div className="mt-1 h-1.5 w-2/3 rounded-full" style={{ background: c.background_text, opacity: 0.4 }} />
      </div>
    </div>
  );
};

export const ThemeGallery: React.FC<{
  selectedTheme: string | null;
  onSelectTheme: (themeId: string | null) => void;
}> = ({ selectedTheme, onSelectTheme }) => {
  const cellClass = (active: boolean) =>
    `flex flex-col gap-2 rounded-xl border p-2 text-left transition-all ${
      active ? "border-[#5141E5] ring-2 ring-[#5141E5]/25" : "border-[#EAECF0] hover:border-[#5141E5]/40"
    }`;

  return (
    <div
      className="grid grid-cols-3 gap-3 sm:grid-cols-4"
      role="radiogroup"
      aria-label="테마 프리셋"
      data-testid="theme-gallery"
    >
      <button
        type="button"
        role="radio"
        aria-checked={selectedTheme === null}
        data-testid="theme-option-none"
        onClick={() => onSelectTheme(null)}
        className={cellClass(selectedTheme === null)}
      >
        <div className="flex h-[68px] w-full items-center justify-center rounded-lg bg-[#F2F4F7] text-xs font-medium text-[#667085]">
          기본값
        </div>
        <span className="truncate px-1 text-xs font-medium text-[#344054]">없음</span>
      </button>

      {DEFAULT_THEMES.map((theme: any) => (
        <button
          key={theme.id}
          type="button"
          role="radio"
          aria-checked={selectedTheme === theme.id}
          title={theme.description || theme.name}
          data-testid={`theme-option-${theme.id}`}
          onClick={() => onSelectTheme(theme.id)}
          className={cellClass(selectedTheme === theme.id)}
        >
          <Swatch theme={theme} />
          <span className="truncate px-1 text-xs font-medium text-[#344054]">{theme.name}</span>
        </button>
      ))}
    </div>
  );
};

export default ThemeGallery;
