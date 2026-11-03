"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { notify } from "@/components/ui/sonner";
import type { SvgTemplateDraft, SvgTemplateFontSubstitution } from "../../services/api/svg-templates";
import { initialFontSubstitutions } from "../../services/api/svg-template-status";

/** Bundled server faces a source font may be replaced with (design D-3). */
const TARGET_WEIGHTS = [400, 700];

export default function SvgTemplateFontSubstitutions({ draft, busy, onApply }: {
  draft: SvgTemplateDraft; busy: boolean;
  /** Starts a re-analysis with the map; resolves false when the request was superseded. */
  onApply: (substitutions: SvgTemplateFontSubstitution[]) => Promise<boolean>;
}) {
  const t = useTranslations("svgTemplateFonts");
  const [items, setItems] = useState(() => initialFontSubstitutions(draft.package));
  const applied = draft.package?.font_substitutions ?? [];
  const pending = draft.package?.unavailable_fonts ?? [];
  if (!items.length) return null;
  const changed = pending.length > 0 || JSON.stringify(items) !== JSON.stringify(initialFontSubstitutions({ frames: [], font_substitutions: applied }));
  const apply = async () => {
    try { await onApply(items); } catch { notify.error(t("applyFailed")); }
  };
  return <div className="space-y-2 rounded border border-slate-200 p-3" aria-label={t("title")}>
    <p className="text-sm font-medium">{t("title")}</p>
    <p className="text-xs text-slate-500">{t("description")}</p>
    {items.map((item, index) => {
      const isApplied = applied.some(entry => entry.source_family === item.sourceFamily && entry.target_weight === item.targetWeight);
      return <label key={item.sourceFamily} className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">{item.sourceFamily}</span><span aria-hidden="true">→</span>
        <span>{item.targetFamily}</span>
        <select aria-label={t("weightLabel", { family: item.sourceFamily })} className="rounded border p-1" value={item.targetWeight} disabled={busy}
          onChange={event => setItems(previous => previous.map((entry, position) => position === index ? { ...entry, targetWeight: Number(event.target.value) } : entry))}>
          {TARGET_WEIGHTS.map(weight => <option key={weight} value={weight}>{weight >= 600 ? t("bold") : t("regular")}</option>)}
        </select>
        <span className="text-slate-500">{isApplied ? t("applied") : t("pending")}</span>
      </label>;
    })}
    <Button type="button" size="sm" variant="outline" disabled={busy || !changed} onClick={() => void apply()}>{t("apply")}</Button>
  </div>;
}
