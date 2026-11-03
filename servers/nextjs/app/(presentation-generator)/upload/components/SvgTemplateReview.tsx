"use client";
import { useState } from "react";
import { resolveBackendAssetUrl } from "@/utils/api";
import { Button } from "@/components/ui/button";
import type { SvgTemplateDraft, SvgTemplateFrameReview } from "../../services/api/svg-templates";
import { frameElementShapeRefs } from "../../services/api/svg-template-status";

export default function SvgTemplateReview({ draft, busy, onSave, onDirtyChange }: {
  draft: SvgTemplateDraft; busy: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onSave: (frames: SvgTemplateFrameReview[], representativeFrameId: string) => Promise<void>;
}) {
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
  return <details className="rounded border border-slate-200 p-3"><summary className="cursor-pointer text-sm font-medium">보존 영역 수정 · 대표 장표 선택</summary>
    <p className="mt-2 text-xs text-slate-500">잘못 분류된 고정 요소만 수정하세요. 본문 구성은 생성할 때 내용에 맞게 결정합니다. 수정하면 새 분석 작업에서 양식과 글꼴을 다시 검사합니다.</p>
    {reviews.map(review => {
      const frame = draft.package?.frames.find(item => item.frame_id === review.frameId);
      const bodyEvidence = draft.package?.body_evidence?.find(item => item.source_page === frame?.source_page);
      const bodyIds = bodyEvidence?.body_shape_ids ?? [];
      const preview = draft.source_previews?.[String(frame?.source_page)];
      const source = frame?.source_canvas?.logicalSize;
      const candidates = [...new Set([...(frame?.elements ?? []).flatMap(frameElementShapeRefs), ...bodyIds])];
      const boundsFor = (id: string) => frame?.elements?.find(item => frameElementShapeRefs(item).includes(id))?.bounds ?? bodyEvidence?.body_bounds?.[bodyIds.indexOf(id)];
      return <fieldset key={review.frameId} className="mt-3 space-y-2 rounded border p-3"><legend className="px-1 text-xs">원본 {(frame?.source_page ?? 0) + 1}장</legend>
        <label className="flex gap-2 text-xs"><input type="radio" name="template-representative" checked={representative === review.frameId} onChange={() => { setRepresentative(review.frameId); setChanged(true); onDirtyChange(true); }} />대표 장표</label>
        <label className="block text-xs">양식 역할<select className="ml-2 rounded border p-1" value={review.role} onChange={event => update(review.frameId, { role: event.target.value as SvgTemplateFrameReview["role"] })}><option value="cover">표지</option><option value="section">구분 장표</option><option value="body">본문</option></select></label>
        {preview && source ? <div className="relative inline-block max-w-full"><img src={resolveBackendAssetUrl(preview)} alt="번호로 보존 요소를 확인할 원본 장표" className="block max-h-72 max-w-full object-contain" />{candidates.map((id, index) => {
          const bounds = boundsFor(id);
          return bounds ? <span key={id} className={`pointer-events-none absolute border ${review.fixedShapeIds.includes(id) ? "border-violet-500 bg-violet-400/10" : "border-slate-400"}`} style={{ left: `${bounds.x / source.width * 100}%`, top: `${bounds.y / source.height * 100}%`, width: `${bounds.width / source.width * 100}%`, height: `${bounds.height / source.height * 100}%` }}><span className="bg-white/90 px-1 text-[10px] text-slate-800">{index + 1}</span></span> : null;
        })}</div> : null}
        <div className="space-y-1"><p className="text-xs font-medium">보존할 원본 요소</p>{candidates.map((id, index) => <label key={id} className="flex gap-2 text-xs"><input type="checkbox" checked={review.fixedShapeIds.includes(id)} onChange={event => update(review.frameId, { fixedShapeIds: event.target.checked ? [...review.fixedShapeIds, id] : review.fixedShapeIds.filter(value => value !== id) })} />요소 {index + 1} · {bodyIds.includes(id) ? "본문으로 분류됨" : "보존 대상으로 분류됨"}</label>)}</div>
        <div className="flex flex-wrap gap-2"><p className="w-full text-xs font-medium">본문 사용 영역 (원본 좌표)</p>{(["x", "y", "width", "height"] as const).map((axis, index) => <label key={axis} className="text-xs">{["가로 위치", "세로 위치", "너비", "높이"][index]}<input className="ml-1 w-20 rounded border p-1" type="number" min="0" step="0.01" value={review.bodyRegion[axis]} onChange={event => update(review.frameId, { bodyRegion: { ...review.bodyRegion, [axis]: Number(event.target.value) } })} /></label>)}</div>
      </fieldset>;
    })}
    <Button className="mt-3" type="button" size="sm" variant="outline" disabled={busy || !changed || reviews.some(review => review.bodyRegion.width <= 0 || review.bodyRegion.height <= 0)} onClick={() => void onSave(reviews, representative)}>수정 내용 저장하고 다시 분석</Button>
  </details>;
}
