"use client";

import React, { useEffect, useRef, useState } from "react";
import { DEFAULT_THEMES } from "./ThemePanel/constants";
import ThemeApi from "@/app/(presentation-generator)/services/api/theme";
import { composeStyledTheme } from "@/app/(presentation-generator)/presentation/utils/composeStyledTheme";
import { applyPresentationThemeToElement } from "@/app/(presentation-generator)/presentation/utils/applyPresentationThemeDom";
import { notify } from "@/components/ui/sonner";
import type { Theme, ThemeData } from "@/app/(presentation-generator)/services/api/types";
import { errorMessage } from "./ThemePanel/themePanelDefaults";

// Palette keys previewed as swatches; all are real `ThemeColors` keys.
const PREVIEW_COLOR_KEYS = [
  "primary",
  "background",
  "card",
  "stroke",
  "background_text",
  "graph_2",
  "graph_5",
] as const;

// C2: compose a new theme = a curated STYLE preset (fonts + typography/shape/
// elevation/density) + a brand COLOUR palette generated from primary/background
// (theme_generate is deterministic colour math — no LLM). Standalone so it doesn't
// touch the 1202-line ThemePanel. Saves via ThemeApi.createTheme.
const ThemeComposer: React.FC = () => {
  const [styleId, setStyleId] = useState<string>(DEFAULT_THEMES[0]?.id ?? "");
  const [primary, setPrimary] = useState("#1f6feb");
  const [background, setBackground] = useState("#ffffff");
  const [name, setName] = useState("");
  const [composedData, setComposedData] = useState<ThemeData | null>(null);
  const [busy, setBusy] = useState(false);
  const previewRef = useRef<HTMLDivElement>(null);

  const style = DEFAULT_THEMES.find((t) => t.id === styleId) ?? DEFAULT_THEMES[0];

  // Live preview: apply the composed theme's tokens to the preview box.
  // `applyPresentationThemeToElement` reads only `.data`; the rest of the Theme
  // fields are placeholders for this throwaway preview object.
  useEffect(() => {
    if (composedData && previewRef.current) {
      const previewTheme: Theme = {
        id: "composer-preview",
        name: "",
        description: "",
        user: "local",
        logo: "",
        data: composedData,
      };
      applyPresentationThemeToElement(previewRef.current, previewTheme);
    }
  }, [composedData]);

  const handleGenerate = async () => {
    setBusy(true);
    try {
      const colors = await ThemeApi.generateTheme({ primary, background });
      // composeStyledTheme returns an untyped bag; it always carries the palette
      // it was handed plus the preset's fonts, which is exactly ThemeData.
      setComposedData(composeStyledTheme(style, colors) as ThemeData);
    } catch (e) {
      notify.error("팔레트 생성 실패", errorMessage(e) || "색상 팔레트를 생성하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const handleSave = async () => {
    if (!composedData) return;
    setBusy(true);
    try {
      await ThemeApi.createTheme({
        name: name.trim() || `${style?.name ?? "Custom"} 스타일`,
        description: `${style?.name ?? ""} 스타일 + 브랜드 색`,
        logo: null,
        data: composedData,
        company_name: null,
      });
      notify.success("테마 저장됨", "내 테마에 추가되었습니다.");
    } catch (e) {
      notify.error("테마 저장 실패", errorMessage(e) || "테마를 저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="w-full rounded-2xl border border-zinc-200/80 bg-white p-5 shadow-sm sm:p-7" data-testid="theme-composer">
      <h2 className="mb-2 text-xl font-semibold tracking-tight text-zinc-900 font-syne">스타일 + 브랜드색으로 테마 만들기</h2>
      <p className="mb-4 text-sm text-[#667085]">큐레이티드 스타일을 고르고 브랜드 색을 넣으면 완성된 테마를 만들어 저장합니다.</p>

      <label className="mb-2 block text-xs font-medium text-[#344054]">스타일</label>
      <div className="mb-4 flex flex-wrap gap-2" role="radiogroup" aria-label="스타일">
        {DEFAULT_THEMES.map((t) => (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={styleId === t.id}
            data-testid={`style-option-${t.id}`}
            onClick={() => setStyleId(t.id)}
            className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-all ${
              styleId === t.id ? "border-brand bg-brand/10 text-brand" : "border-[#EAECF0] text-[#344054] hover:border-brand/40"
            }`}
          >
            {t.name}
          </button>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-xs font-medium text-[#344054]">
          기본색(primary)
          <input type="color" data-testid="composer-primary" value={primary} onChange={(e) => setPrimary(e.target.value)} className="h-11 w-16 cursor-pointer rounded-lg border border-zinc-200 bg-white p-1" />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-[#344054]">
          배경색(background)
          <input type="color" data-testid="composer-background" value={background} onChange={(e) => setBackground(e.target.value)} className="h-11 w-16 cursor-pointer rounded-lg border border-zinc-200 bg-white p-1" />
        </label>
        <label className="flex flex-1 flex-col gap-1 text-xs font-medium text-[#344054]">
          테마 이름(선택)
          <input type="text" data-testid="composer-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={`${style?.name ?? "Custom"} 스타일`} className="h-11 rounded-xl border border-zinc-200 bg-white px-3 text-sm outline-none focus:border-zinc-400 focus:ring-2 focus:ring-zinc-900/10" />
        </label>
        <button type="button" data-testid="composer-generate" disabled={busy} onClick={handleGenerate} className="h-11 rounded-xl bg-zinc-900 px-5 text-sm font-medium text-white transition-colors hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-500 focus-visible:ring-offset-2 disabled:opacity-50">
          생성
        </button>
      </div>

      {composedData && (
        <div className="mb-4">
          <div className="mb-2 flex flex-wrap gap-1" data-testid="composer-palette">
            {PREVIEW_COLOR_KEYS.map((k) => (
              <span key={k} title={k} className="h-6 w-6 rounded border border-black/10" style={{ background: composedData.colors?.[k] }} />
            ))}
          </div>
          <div ref={previewRef} className="rounded-lg p-4" style={{ background: "var(--background-color)" }} data-testid="composer-preview">
            <div className="p-4" style={{ background: "var(--card-color)", border: "1px solid var(--border-color)", borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-md)" }}>
              <div style={{ fontFamily: "var(--heading-font-family)", color: "var(--background-text)", fontSize: "var(--fs-h3)", fontWeight: "var(--fw-heading)", letterSpacing: "var(--ls-heading)" }}>
                미리보기 제목
              </div>
              <p style={{ fontFamily: "var(--body-font-family)", color: "var(--muted-color)", fontSize: "var(--fs-body)", lineHeight: "var(--lh-body)" }}>
                이 스타일과 브랜드 색이 적응형 슬라이드에 이렇게 적용됩니다.
              </p>
              <div className="mt-2 inline-block rounded-full px-3 py-1 text-xs" style={{ background: "var(--primary-color)", color: "var(--primary-text)" }}>
                강조 칩
              </div>
            </div>
          </div>
          <button type="button" data-testid="composer-save" disabled={busy} onClick={handleSave} className="mt-4 h-11 rounded-xl border border-zinc-200 bg-white px-5 text-sm font-medium text-zinc-800 transition-colors hover:bg-zinc-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 focus-visible:ring-offset-2 disabled:opacity-50">
            내 테마로 저장
          </button>
        </div>
      )}
    </div>
  );
};

export default ThemeComposer;
