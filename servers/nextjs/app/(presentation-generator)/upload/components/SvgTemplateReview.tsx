"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { resolveBackendAssetUrl } from "@/utils/api";
import { Button } from "@/components/ui/button";
import type { SvgTemplateDraft, SvgTemplateFrameReview } from "../../services/api/svg-templates";
import { frameElementShapeRefs } from "../../services/api/svg-template-status";

export default function SvgTemplateReview({ draft, busy, onSave, onDirtyChange }: {
  draft: SvgTemplateDraft; busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onSave: (frames: SvgTemplateFrameReview[], representativeFrameId: string) => Promise<void>;
}) {
  const t = useTranslations("svgTemplatePicker");
  const [reviews, setReviews] = useState<SvgTemplateFrameReview[]>(() => (draft.package?.frames ?? []).map(frame => ({
    frameId: frame.frame_id,
    fixedShapeIds: [...new Set((frame.elements ?? []).flatMap(frameElementShapeRefs))],
    bodyRegion: frame.body_region ?? { x: 0, y: 0, width: 0, height: 0 },
    role: frame.role ?? "body",
  })));
  const [representative, setRepresentative] = useState(reviews[0]?.frameId ?? "");
  const [changed, setChanged] = useState(false);
  const update = (id: string, patch: Partial<SvgTemplateFrameReview>) => { setReviews(previous => previous.map(review => review.frameId === id ? { ...review, ...patch } : review)); setChanged(true); onDirtyChange(true); };
  if (!reviews.length) return null;
  return <details className="rounded border border-slate-200 p-3"><summary className="cursor-pointer text-sm font-medium">{t("reviewSummary")}</summary>
    <p className="mt-2 text-xs text-slate-500">{t("reviewHelp")}</p>
    {reviews.map(review => {
      const frame = draft.package?.frames.find(item => item.frame_id === review.frameId);
      const bodyEvidence = draft.package?.body_evidence?.find(item => item.source_page === frame?.source_page);
      const bodyIds = bodyEvidence?.body_shape_ids ?? [];
      const preview = draft.source_previews?.[String(frame?.source_page)];
      const source = frame?.source_canvas?.logicalSize;
      const candidates = [...new Set([...(frame?.elements ?? []).flatMap(frameElementShapeRefs), ...bodyIds])];
      const boundsFor = (id: string) => frame?.elements?.find(item => frameElementShapeRefs(item).includes(id))?.bounds ?? bodyEvidence?.body_bounds?.[bodyIds.indexOf(id)];
      return <fieldset key={review.frameId} className="mt-3 space-y-2 rounded border p-3"><legend className="px-1 text-xs">{t("reviewSourceLegend", { page: (frame?.source_page ?? 0) + 1 })}</legend>
        <label className="flex gap-2 text-xs"><input type="radio" name="template-representative" checked={representative === review.frameId} onChange={() => { setRepresentative(review.frameId); setChanged(true); onDirtyChange(true); }} />{t("reviewRepresentative")}</label>
        <label className="block text-xs">{t("reviewRole")}<select className="ml-2 rounded border p-1" value={review.role} onChange={event => update(review.frameId, { role: event.target.value as SvgTemplateFrameReview["role"] })}><option value="cover">{t("reviewRoleCover")}</option><option value="section">{t("reviewRoleSection")}</option><option value="body">{t("reviewRoleBody")}</option></select></label>
        {preview && source ? <div className="relative inline-block max-w-full"><img src={resolveBackendAssetUrl(preview)} alt={t("reviewSourceImageAlt")} className="block max-h-72 max-w-full object-contain" />{candidates.map((id, index) => {
          const bounds = boundsFor(id);
          return bounds ? <span key={id} className={`pointer-events-none absolute border ${review.fixedShapeIds.includes(id) ? "border-violet-500 bg-violet-400/10" : "border-slate-400"}`} style={{ left: `${bounds.x / source.width * 100}%`, top: `${bounds.y / source.height * 100}%`, width: `${bounds.width / source.width * 100}%`, height: `${bounds.height / source.height * 100}%` }}><span className="bg-white/90 px-1 text-[10px] text-slate-800">{index + 1}</span></span> : null;
        })}</div> : null}
        <div className="space-y-1"><p className="text-xs font-medium">{t("reviewPreservedHeading")}</p>{candidates.map((id, index) => <label key={id} className="flex gap-2 text-xs"><input type="checkbox" checked={review.fixedShapeIds.includes(id)} onChange={event => update(review.frameId, { fixedShapeIds: event.target.checked ? [...review.fixedShapeIds, id] : review.fixedShapeIds.filter(value => value !== id) })} />{t("reviewElementLabel", { index: index + 1, classification: bodyIds.includes(id) ? t("reviewClassifiedBody") : t("reviewClassifiedPreserved") })}</label>)}</div>
        <div className="flex flex-wrap gap-2"><p className="w-full text-xs font-medium">{t("reviewBodyRegionHeading")}</p>{(["x", "y", "width", "height"] as const).map((axis, index) => <label key={axis} className="text-xs">{[t("reviewAxisX"), t("reviewAxisY"), t("reviewAxisWidth"), t("reviewAxisHeight")][index]}<input className="ml-1 w-20 rounded border p-1" type="number" min="0" step="0.01" value={review.bodyRegion[axis]} onChange={event => update(review.frameId, { bodyRegion: { ...review.bodyRegion, [axis]: Number(event.target.value) } })} /></label>)}</div>
      </fieldset>;
    })}
    <Button className="mt-3" type="button" size="sm" variant="outline" disabled={busy || !changed || reviews.some(review => review.bodyRegion.width <= 0 || review.bodyRegion.height <= 0)} onClick={() => void onSave(reviews, representative)}>{t("reviewSaveAndReanalyze")}</Button>
  </details>;
}
