"use client";

import React, { useEffect, useRef, useState } from "react";
import { DEFAULT_THEMES } from "./ThemePanel/constants";
import ThemeApi from "@/app/(presentation-generator)/services/api/theme";
import { composeStyledTheme } from "@/app/(presentation-generator)/presentation/utils/composeStyledTheme";
import { applyPresentationThemeToElement } from "@/app/(presentation-generator)/presentation/utils/applyPresentationThemeDom";
import { notify } from "@/components/ui/sonner";

// C2: compose a new theme = a curated STYLE preset (fonts + typography/shape/
// elevation/density) + a brand COLOUR palette generated from primary/background
// (theme_generate is deterministic colour math — no LLM). Standalone so it doesn't
// touch the 1202-line ThemePanel. Saves via ThemeApi.createTheme.
const ThemeComposer: React.FC = () => {
  const [styleId, setStyleId] = useState<string>(DEFAULT_THEMES[0]?.id ?? "");
  const [primary, setPrimary] = useState("#1f6feb");
  const [background, setBackground] = useState("#ffffff");
  const [name, setName] = useState("");
  const [composedData, setComposedData] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const previewRef = useRef<HTMLDivElement>(null);

  const style = DEFAULT_THEMES.find((t: any) => t.id === styleId) ?? DEFAULT_THEMES[0];

  // Live preview: apply the composed theme's tokens to the preview box.
  useEffect(() => {
    if (composedData && previewRef.current) {
      applyPresentationThemeToElement(previewRef.current, { data: composedData } as any);
    }
  }, [composedData]);

  const handleGenerate = async () => {
    setBusy(true);
    try {
      const colors = await ThemeApi.generateTheme({ primary, background });
      setComposedData(composeStyledTheme(style as any, colors));
    } catch (e: any) {
      notify.error("팔레트 생성 실패", e?.message || "색상 팔레트를 생성하지 못했습니다.");
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
      } as any);
      notify.success("테마 저장됨", "내 테마에 추가되었습니다.");
    } catch (e: any) {
      notify.error("테마 저장 실패", e?.message || "테마를 저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="w-full" data-testid="theme-composer">
      <h2 className="mb-1 text-base font-semibold text-[#101828] font-syne">스타일 + 브랜드색으로 테마 만들기</h2>
      <p className="mb-4 text-sm text-[#667085]">큐레이티드 스타일을 고르고 브랜드 색을 넣으면 완성된 테마를 만들어 저장합니다.</p>

      <label className="mb-2 block text-xs font-medium text-[#344054]">스타일</label>
      <div className="mb-4 flex flex-wrap gap-2" role="radiogroup" aria-label="스타일">
        {DEFAULT_THEMES.map((t: any) => (
          <button
            key={t.id}
            type="button"
            role="radio"
            aria-checked={styleId === t.id}
            data-testid={`style-option-${t.id}`}
            onClick={() => setStyleId(t.id)}
            className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-all ${
              styleId === t.id ? "border-[#5141E5] bg-[#5141E5]/10 text-[#5141E5]" : "border-[#EAECF0] text-[#344054] hover:border-[#5141E5]/40"
            }`}
          >
            {t.name}
          </button>
        ))}
      </div>

      <div className="mb-4 flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-xs font-medium text-[#344054]">
          기본색(primary)
          <input type="color" data-testid="composer-primary" value={primary} onChange={(e) => setPrimary(e.target.value)} className="h-9 w-16 cursor-pointer rounded border border-[#EAECF0]" />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-[#344054]">
          배경색(background)
          <input type="color" data-testid="composer-background" value={background} onChange={(e) => setBackground(e.target.value)} className="h-9 w-16 cursor-pointer rounded border border-[#EAECF0]" />
        </label>
        <label className="flex flex-1 flex-col gap-1 text-xs font-medium text-[#344054]">
          테마 이름(선택)
          <input type="text" data-testid="composer-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={`${style?.name ?? "Custom"} 스타일`} className="h-9 rounded border border-[#EAECF0] px-2 text-sm" />
        </label>
        <button type="button" data-testid="composer-generate" disabled={busy} onClick={handleGenerate} className="h-9 rounded-full bg-[#5141E5] px-4 text-sm font-medium text-white disabled:opacity-50">
          생성
        </button>
      </div>

      {composedData && (
        <div className="mb-4">
          <div className="mb-2 flex flex-wrap gap-1" data-testid="composer-palette">
            {["primary", "background", "card", "stroke", "background_text", "graph_2", "graph_5"].map((k) => (
              <span key={k} title={k} className="h-6 w-6 rounded border border-black/10" style={{ background: composedData.colors?.[k] }} />
            ))}
          </div>
          <div ref={previewRef} className="rounded-lg p-4" style={{ background: "var(--background-color)" }} data-testid="composer-preview">
            <div className="p-4" style={{ background: "var(--card-color)", border: "1px solid var(--border-color)", borderRadius: "var(--radius-lg)", boxShadow: "var(--shadow-md)" }}>
              <div style={{ fontFamily: "var(--heading-font-family)", color: "var(--background-text)", fontSize: "var(--fs-h3)", fontWeight: "var(--fw-heading)" as any, letterSpacing: "var(--ls-heading)" }}>
                미리보기 제목
              </div>
              <p style={{ fontFamily: "var(--body-font-family)", color: "var(--muted-color)", fontSize: "var(--fs-body)", lineHeight: "var(--lh-body)" as any }}>
                이 스타일과 브랜드 색이 적응형 슬라이드에 이렇게 적용됩니다.
              </p>
              <div className="mt-2 inline-block rounded-full px-3 py-1 text-xs" style={{ background: "var(--primary-color)", color: "var(--primary-text)" }}>
                강조 칩
              </div>
            </div>
          </div>
          <button type="button" data-testid="composer-save" disabled={busy} onClick={handleSave} className="mt-3 h-9 rounded-full border border-[#5141E5] px-4 text-sm font-medium text-[#5141E5] disabled:opacity-50">
            내 테마로 저장
          </button>
        </div>
      )}
    </div>
  );
};

export default ThemeComposer;
