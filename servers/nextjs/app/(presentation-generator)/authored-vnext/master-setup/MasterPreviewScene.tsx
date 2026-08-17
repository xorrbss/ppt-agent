import type { CSSProperties } from "react";
import { useTranslations } from "next-intl";

import type { PreviewScene, PreviewSceneItem } from "@/lib/authored-vnext/master/view-model";

function itemStyle(item: PreviewSceneItem, scene: PreviewScene): CSSProperties {
  const furniture = item.layer === "masterFurniture";
  return {
    position: "absolute",
    left: `${item.bounds.x / scene.width * 100}%`,
    top: `${item.bounds.y / scene.height * 100}%`,
    width: `${item.bounds.width / scene.width * 100}%`,
    height: `${Math.max(item.bounds.height / scene.height * 100, .2)}%`,
    border: item.role === "line" ? 0 : furniture ? "1px solid rgba(29, 78, 216, .5)" : "1px dashed rgba(4, 120, 87, .8)",
    borderTop: item.role === "line" ? "1px solid #94a3b8" : undefined,
    background: furniture ? item.role === "logo" ? "transparent" : "rgba(219, 234, 254, .7)" : "rgba(209, 250, 229, .55)",
    overflow: "hidden",
  };
}

interface MasterPreviewSceneProps { scene: PreviewScene; compact?: boolean; label?: string; logoUrl?: string | null; }

/** Small, read-only sample preview. The studio canvas owns direct manipulation. */
export default function MasterPreviewScene({ scene, compact = false, label, logoUrl }: MasterPreviewSceneProps) {
  const t = useTranslations("authoredVnext.master");
  const studio = useTranslations("authoredVnext.master.studio");
  const accessibleLabel = label ?? t("preview.defaultLabel");
  return <figure className="space-y-2">
    <div aria-label={t("preview.canvasAria", { label: accessibleLabel, layout: scene.layoutId })} className="relative aspect-video w-full overflow-hidden rounded-xl border border-slate-300 shadow-sm" data-layout-id={scene.layoutId} style={{ background: scene.background }}>
      {scene.items.map((item) => <div aria-label={t("preview.itemAria", { item: item.label, state: item.locked ? t("preview.lockedItem") : t("preview.editableItem") })} className={`flex items-center px-1 leading-tight ${compact ? "text-[6px] sm:text-[8px]" : "text-[7px] sm:text-[10px]"}`} data-layer={item.layer} data-locked={String(item.locked)} data-model-writable={String(item.modelWritable)} key={item.id} style={itemStyle(item, scene)} title={t("preview.itemTitle", { id: item.id, layer: item.layer })}>
        {item.role === "logo" ? logoUrl ? <img alt={studio("brandLogo")} className="h-full w-full object-contain" src={logoUrl} /> : <span aria-label={studio("noLogo")} className="flex h-full w-full items-center justify-center text-[10px]">+</span> : item.role === "title" ? <span className="font-bold text-slate-950">{scene.titleLines.map((line) => <span className="block" key={line}>{line}</span>)}</span> : item.role === "body" ? <span className="text-slate-700">{scene.body}</span> : <span>{item.label}</span>}
      </div>)}
      {!compact && <div className="absolute bottom-2 right-2 flex gap-2 rounded bg-white/95 px-2 py-1 text-[9px] text-slate-700 shadow-sm"><span className="font-semibold text-blue-800">{t("preview.masterLegend")}</span><span className="font-semibold text-emerald-800">{t("preview.bodyLegend")}</span></div>}
    </div>
    <figcaption className="text-xs text-slate-600">{t("preview.caption", { label: accessibleLabel, layout: scene.layoutId })}</figcaption>
  </figure>;
}
