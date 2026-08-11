"use client";

import { createPortal } from "react-dom";
import { Sparkles } from "lucide-react";
import { useTranslations } from "next-intl";

import type { SelectionRect } from "./smartHtmlEditorConstants";

export function SmartHtmlSelectionOverlay({ hoverRect, selectionRect }: { hoverRect: SelectionRect | null; selectionRect: SelectionRect | null }) {
  const t = useTranslations("community");
  if (typeof document === "undefined") return null;
  return createPortal(<>
    {hoverRect && <div aria-hidden="true" className="pointer-events-none fixed z-[80] rounded-[8px] border-2 border-dotted border-[#7A5AF8]" style={{ ...hoverRect, backgroundColor: "rgba(122, 90, 248, 0.07)", backgroundImage: "radial-gradient(rgba(122, 90, 248, 0.38) 1px, transparent 1px)", backgroundSize: "8px 8px", boxShadow: "0 0 0 1px rgba(255,255,255,0.9), 0 0 0 5px rgba(122,90,248,0.12)" }} />}
    {selectionRect && <div aria-hidden="true" className="pointer-events-none fixed z-[81] rounded-[8px] border-2 border-dotted border-[#6941C6]" style={{ ...selectionRect, backgroundColor: "rgba(105, 65, 198, 0.08)", backgroundImage: "radial-gradient(rgba(105, 65, 198, 0.42) 1px, transparent 1px)", backgroundSize: "8px 8px", boxShadow: "0 0 0 1px rgba(255,255,255,0.95), 0 0 0 5px rgba(105,65,198,0.16)" }}><span className="absolute -top-8 left-0 inline-flex items-center gap-1.5 whitespace-nowrap rounded-md bg-[#6941C6] px-2 py-1.5 font-syne text-[11px] font-semibold text-white shadow-sm"><Sparkles className="h-3.5 w-3.5" />{t("selectedForAi")}</span></div>}
  </>, document.body);
}
