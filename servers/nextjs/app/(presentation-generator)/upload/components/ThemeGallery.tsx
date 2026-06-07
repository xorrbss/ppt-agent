import React, { useEffect, useState } from "react";
import { DEFAULT_THEMES } from "@/app/(presentation-generator)/(dashboard)/theme/components/ThemePanel/constants";
import ThemeApi from "@/app/(presentation-generator)/services/api/theme";

// Theme-preset gallery for the new-deck flow. Picking a preset stores its id; the
// upload handler PATCHes the full theme onto the created deck so it renders with
// that look. Each swatch reflects colour + shape (radius) + elevation (shadow) —
// the dimensions that distinguish the v2 presets — without loading every font.
// Built-in presets plus the user's saved custom themes (best-effort fetch).

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

const cellClass = (active: boolean) =>
  `flex flex-col gap-2 rounded-xl border p-2 text-left transition-all ${
    active ? "border-[#5141E5] ring-2 ring-[#5141E5]/25" : "border-[#EAECF0] hover:border-[#5141E5]/40"
  }`;

const ThemeCell: React.FC<{ theme: any; active: boolean; onSelect: () => void }> = ({ theme, active, onSelect }) => (
  <button
    type="button"
    role="radio"
    aria-checked={active}
    title={theme.description || theme.name}
    data-testid={`theme-option-${theme.id}`}
    onClick={onSelect}
    className={cellClass(active)}
  >
    <Swatch theme={theme} />
    <span className="truncate px-1 text-xs font-medium text-[#344054]">{theme.name}</span>
  </button>
);

export const ThemeGallery: React.FC<{
  selectedTheme: string | null;
  onSelectTheme: (themeId: string | null) => void;
}> = ({ selectedTheme, onSelectTheme }) => {
  const [customThemes, setCustomThemes] = useState<any[]>([]);

  useEffect(() => {
    let cancelled = false;
    // Best-effort: show the user's saved themes too. On failure (e.g. unauthed /
    // offline) just keep the built-in presets.
    ThemeApi.getThemes()
      .then((list) => {
        if (!cancelled) setCustomThemes((list || []).filter((t: any) => t?.data?.colors?.background));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div role="radiogroup" aria-label="테마 프리셋" data-testid="theme-gallery">
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
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
          <ThemeCell key={theme.id} theme={theme} active={selectedTheme === theme.id} onSelect={() => onSelectTheme(theme.id)} />
        ))}
      </div>

      {customThemes.length > 0 && (
        <div className="mt-4" data-testid="theme-gallery-custom">
          <p className="mb-2 text-xs font-medium text-[#667085]">내 테마</p>
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-4">
            {customThemes.map((theme: any) => (
              <ThemeCell key={theme.id} theme={theme} active={selectedTheme === theme.id} onSelect={() => onSelectTheme(theme.id)} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default ThemeGallery;
