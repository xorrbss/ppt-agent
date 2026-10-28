"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useDispatch, useSelector } from "react-redux";
import { Button } from "@/components/ui/button";
import { notify } from "@/components/ui/sonner";
import type { RootState } from "@/store/store";
import { setPptGenUploadState, invalidateSvgTemplateSession } from "@/store/slices/presentationGenUpload";
import { resolveBackendAssetUrl } from "@/utils/api";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { SvgTemplatesApi, svgTemplateCanvas, type SvgTemplateCard, type SvgTemplateDraft, type SvgTemplateCapability, type SvgTemplateMode } from "../../services/api/svg-templates";
import { AUTH_SESSION_EXPIRED_EVENT } from "../../services/api/api-error-handler";
import { templateReason, templateIssueText, templateFontText, templateRoleText, templateOperationText, templateActionText, frameReviewBlockReason } from "../../services/api/svg-template-status";
import SvgTemplateReview from "./SvgTemplateReview";
import type { PresentationConfig } from "../type";

const stateLabels = { analyzing: "분석 중", "review-required": "확인 필요", ready: "사용 가능", failed: "분석 실패", archived: "보관됨" };
const modeLabels = { "template-redesign": "양식 기반 재디자인", "free-design": "자유 디자인" };
const modeDescriptions = {
  "template-redesign": "원본 글꼴과 상단·하단을 유지하고 좋은 본문 구성을 살려 내용에 맞게 개선합니다",
  "free-design": "템플릿의 디자인 특징을 참고해 전체 구도를 새롭게 구성합니다",
};

export default function SvgTemplatePicker({ config, onConfigChange, sourceContentMode, hasCompanyMaster }: {
  config: PresentationConfig; onConfigChange: (key: keyof PresentationConfig, value: unknown) => void;
  sourceContentMode: boolean; hasCompanyMaster: boolean;
}) {
  const dispatch = useDispatch();
  const storedSelection = useSelector((state: RootState) => state.pptGenUpload.svgTemplateSelection);
  const selection = useMemo(() => storedSelection ?? (config.svgTemplatePin && config.svgTemplateMode ? {
    pin: config.svgTemplatePin, mode: config.svgTemplateMode, name: config.svgTemplateName ?? "저장된 내 템플릿", kind: config.svgTemplateKind ?? "framed" as const,
  } : null), [storedSelection, config.svgTemplatePin, config.svgTemplateMode, config.svgTemplateName, config.svgTemplateKind]);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [cards, setCards] = useState<SvgTemplateCard[]>([]);
  const [pinnedCard, setPinnedCard] = useState<SvgTemplateCard | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<SvgTemplateDraft | null>(null);
  const [selectedFrames, setSelectedFrames] = useState<string[]>([]);
  const [reviewDirty, setReviewDirty] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [preparation, setPreparation] = useState<{ operationId: string; state: string; errorCode?: string } | null>(null);
  const [capability, setCapability] = useState<SvgTemplateCapability | null>(null);
  const capabilityEpoch = useRef(0);
  const ownerEpoch = useRef(0);
  const preparationEpoch = useRef(0);
  const ownerAlive = useRef(true);
  const [ownerInvalidated, setOwnerInvalidated] = useState(false);
  const live = (epoch: number) => ownerAlive.current && ownerEpoch.current === epoch;
  useEffect(() => {
    ownerAlive.current = true;
    const invalidate = () => {
      ownerEpoch.current += 1; ownerAlive.current = false; capabilityEpoch.current += 1;
      setOwnerInvalidated(true); setAvailable(false); setCards([]); setPinnedCard(null); setDraft(null); setPreparation(null); setCapability(null);
      setFile(null); setName(""); setBusy(false); setConfirmed(false); setReviewDirty(false);
      dispatch(invalidateSvgTemplateSession());
      onConfigChange("svgTemplatePin", undefined); onConfigChange("svgTemplateMode", undefined);
      onConfigChange("svgTemplateFrameMap", undefined); onConfigChange("svgTemplateCapabilityState", undefined);
    };
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, invalidate);
    window.addEventListener("presenton:auth-logout", invalidate);
    return () => { ownerEpoch.current += 1; ownerAlive.current = false; window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, invalidate); window.removeEventListener("presenton:auth-logout", invalidate); };
  // A session boundary permanently invalidates this mounted picker.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dispatch]);
  const canvas = svgTemplateCanvas(config);
  const canvasKey = JSON.stringify(canvas);
  useEffect(() => { preparationEpoch.current += 1; setPreparation(null); }, [canvasKey, selection?.pin.digest, selection?.mode]);
  const catalogSelection = cards.find(card => card.templateId === selection?.pin.templateId && card.pin?.digest === selection.pin.digest);
  const selected = catalogSelection ?? (pinnedCard?.pin?.digest === selection?.pin.digest ? pinnedCard : undefined);
  useEffect(() => {
    let cancelled = false;
    const owner = ownerEpoch.current;
    setPinnedCard(null);
    if (!selection || catalogSelection || !live(owner)) return;
    SvgTemplatesApi.version(selection.pin).then(result => {
      if (!cancelled && live(owner) && result.template.pin?.digest === selection.pin.digest) setPinnedCard(result.template);
    }).catch(() => { /* Capability admission remains fail closed; no head substitution. */ });
    return () => { cancelled = true; };
  // Mode availability belongs to the selected immutable revision, not the current catalogue head.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection?.pin.templateId, selection?.pin.revision, selection?.pin.digest, catalogSelection, refresh]);
  const hasCommonTitleDesign = config.commonHeaderMode !== "free";

  useEffect(() => {
    let cancelled = false;
    const owner = ownerEpoch.current;
    if (!live(owner)) return;
    setLoading(true);
    SvgTemplatesApi.availability().then(async availability => {
      if (cancelled || !live(owner)) return;
      setAvailable(availability.enabled); setError("");
      if (!availability.enabled) { setCards([]); return; }
      const result = await SvgTemplatesApi.list();
      if (!cancelled && live(owner)) setCards(result.templates);
    })
      .catch(() => { if (!cancelled && live(owner)) { setAvailable(false); setCards([]); setError("내 템플릿을 불러오지 못했습니다."); } })
      .finally(() => { if (!cancelled && live(owner)) setLoading(false); });
    return () => { cancelled = true; };
  }, [refresh]);

  useEffect(() => {
    const owner = ownerEpoch.current;
    if (!live(owner)) return;
    const epoch = ++capabilityEpoch.current;
    setCapability(null);
    if (!selection) { onConfigChange("svgTemplateCapabilityState", undefined); return; }
    onConfigChange("svgTemplateCapabilityState", "checking");
    if (available === null) return;
    SvgTemplatesApi.capability({ templatePin: selection.pin, mode: selection.mode, canvas,
      ...(sourceContentMode ? { sourceMode: "source_content_restyle" } : {}),
      hasCompanyMaster, hasCommonTitleDesign,
    }).then(result => {
      if (epoch !== capabilityEpoch.current || !live(owner)) return;
      const resolved = available === false ? { ...result, state: "blocked" as const, reasonCode: "svg_template_registration_disabled", nextAction: null } : result;
      setCapability(resolved); onConfigChange("svgTemplateCapabilityState", resolved.state);
    }).catch(() => {
      if (epoch !== capabilityEpoch.current || !live(owner)) return;
      setCapability({ state: "blocked", reasonCode: "사용 가능 여부를 확인하지 못했습니다.", nextAction: "다시 확인해 주세요.", contractDigest: "" });
      onConfigChange("svgTemplateCapabilityState", "blocked");
    });
    return () => { capabilityEpoch.current += 1; };
  // Configuration updates are passed by the upload form; only capability inputs invalidate a result.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, canvasKey, sourceContentMode, hasCompanyMaster, hasCommonTitleDesign, available, refresh]);

  useEffect(() => {
    const owner = ownerEpoch.current;
    if (!live(owner) || !preparation || !["queued", "running", "pending"].includes(preparation.state)) return;
    const revision = preparationEpoch.current;
    const timer = window.setTimeout(() => {
      SvgTemplatesApi.operation(preparation.operationId).then(result => {
        if (!live(owner) || revision !== preparationEpoch.current) return;
        setPreparation({ operationId: preparation.operationId, state: result.state, errorCode: result.errorCode });
        if (["succeeded", "completed"].includes(result.state)) setRefresh(value => value + 1);
      }).catch(() => { if (live(owner) && revision === preparationEpoch.current) setPreparation({ ...preparation, state: "unknown", errorCode: "상태를 확인하지 못했습니다. 작업 기록은 유지됩니다." }); });
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [preparation]);
  const prepare = async () => {
    if (!selection || busy || !ownerAlive.current || available !== true) return;
    const owner = ownerEpoch.current;
    setBusy(true);
    const revision = preparationEpoch.current;
    try { const result = await SvgTemplatesApi.prepare({ templatePin: selection.pin, canvas }); if (live(owner) && revision === preparationEpoch.current) setPreparation(result); }
    catch { if (live(owner)) notify.error("이 크기의 양식 준비를 접수하지 못했습니다."); }
    finally { if (live(owner)) setBusy(false); }
  };
  const choose = (card: SvgTemplateCard) => {
    if (!ownerAlive.current || available !== true || !card.pin || card.registrationState !== "ready") return;
    setPreparation(null);
    dispatch(setPptGenUploadState({ svgTemplateSelection: { pin: card.pin, mode: card.defaultMode, name: card.name, kind: card.kind } }));
    onConfigChange("svgDesignReferenceSnapshotId", undefined);
    onConfigChange("svgDesignReferencePin", undefined);
    onConfigChange("svgDesignReferencePending", false);
    if (card.kind === "framed" && card.defaultMode === "template-redesign") onConfigChange("commonHeaderMode", "free");
  };
  const changeMode = (mode: SvgTemplateMode) => {
    if (!selection || !ownerAlive.current || available !== true) return;
    setPreparation(null);
    dispatch(setPptGenUploadState({ svgTemplateSelection: { ...selection, mode } }));
    if (mode === "template-redesign") onConfigChange("commonHeaderMode", "free");
  };
  const openDraft = async (id: string) => {
    if (!ownerAlive.current) return;
    const owner = ownerEpoch.current;
    setBusy(true);
    try { const result = await SvgTemplatesApi.draft(id); if (!live(owner)) return; setDraft(result); setReviewDirty(false); setSelectedFrames(result.package?.frames.filter(frame => !frameReviewBlockReason(result, frame)).map(frame => frame.frame_id) ?? []); setConfirmed(false); }
    catch { if (live(owner)) notify.error("분석 결과를 불러오지 못했습니다."); }
    finally { if (live(owner)) setBusy(false); }
  };
  const register = async () => {
    if (!file || !name.trim() || busy || !ownerAlive.current || available !== true) return;
    const owner = ownerEpoch.current;
    setBusy(true);
    try {
      const admission = await PresentationGenerationApi.uploadDoc([file], `svg-template:${crypto.randomUUID()}`);
      if (!live(owner)) return;
      const result = await SvgTemplatesApi.register({ name: name.trim(), referenceSnapshotId: admission.referenceSnapshotId, registrationMode: "framed" });
      if (!live(owner)) return;
      setReviewDirty(false);
      setDraft({ template: result.template, operationId: result.operationId, package: null });
      setRefresh(value => value + 1); setConfirmed(false);
    } catch { if (live(owner)) notify.error("템플릿 분석을 접수하지 못했습니다."); }
    finally { if (live(owner)) setBusy(false); }
  };
  const framePreview = (frame: NonNullable<SvgTemplateDraft["package"]>["frames"][number]) => {
    const url = frame.preview_url || draft?.source_previews?.[String(frame.source_page)];
    if (!url) return <p className="mt-2 text-xs text-amber-800">실제 원본 미리보기가 준비되지 않았습니다.</p>;
    const source = frame.source_canvas?.logicalSize;
    const region = frame.body_region;
    const frameUrl = draft?.source_previews?.[`frame:${frame.frame_id}`];
    return <div className="mt-2 grid gap-3 sm:grid-cols-2"><div><p className="mb-1 text-xs font-medium">원본 장표</p><div className="relative inline-block max-w-full">
      <img src={resolveBackendAssetUrl(url)} alt={`원본 ${(frame.source_page ?? 0) + 1}장: 보존 양식과 본문 영역 확인`} className="block max-h-64 max-w-full object-contain" />
      {source && region ? <span aria-label="본문 사용 영역" className="pointer-events-none absolute border-2 border-blue-500 bg-blue-400/10" style={{ left: `${region.x / source.width * 100}%`, top: `${region.y / source.height * 100}%`, width: `${region.width / source.width * 100}%`, height: `${region.height / source.height * 100}%` }} /> : null}
    </div></div><div><p className="mb-1 text-xs font-medium">보존할 양식</p>{frameUrl ? <img src={resolveBackendAssetUrl(frameUrl)} alt="상단·하단과 배경 등 보존할 양식의 실제 렌더" className="block max-h-64 max-w-full object-contain" /> : <p className="text-xs text-amber-800">양식 미리보기가 준비되지 않았습니다.</p>}</div></div>;
  };
  const publish = async (styleOnly = false) => {
    if (!draft || busy || !confirmed || reviewDirty || !ownerAlive.current || available !== true) return;
    if (!styleOnly && (!selectedFrames.length || selectedFrames.some(id => {
      const frame = draft.package?.frames.find(item => item.frame_id === id);
      return !frame || Boolean(frameReviewBlockReason(draft, frame));
    }))) return;
    const owner = ownerEpoch.current;
    setBusy(true);
    try {
      const result = await SvgTemplatesApi.publish(draft.template.templateId, { operationId: draft.operationId, expectedHeadRevision: draft.template.pin?.revision ?? 0, selectedFrameIds: selectedFrames, confirmReview: confirmed, ...(styleOnly ? { styleOnly: true } : {}) });
      if (!live(owner)) return;
      setDraft(null); setRefresh(value => value + 1); if (result.template) choose(result.template);
    } catch { if (live(owner)) notify.error("템플릿을 등록하지 못했습니다. 분석 결과와 준비 상태를 확인해 주세요."); }
    finally { if (live(owner)) setBusy(false); }
  };

  if (ownerInvalidated) return <p role="status" className="mt-4 text-sm text-slate-500">세션이 변경되었습니다. 템플릿을 다시 불러오려면 페이지를 새로고침해 주세요.</p>;
  if (available === false && !selection) return null;
  return <section className="mt-4 space-y-4 rounded-xl border border-slate-200 p-4" aria-label="내 PPT 템플릿">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">내 PPT 템플릿</h3><Button type="button" size="sm" variant="ghost" onClick={() => setRefresh(value => value + 1)}>새로고침</Button></div>
    {loading ? <p role="status" className="text-xs text-slate-500">템플릿을 불러오는 중입니다.</p> : null}
    {error ? <p role="alert" className="text-sm text-rose-700">{error}</p> : null}
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {cards.filter(card => card.provider === "user" && card.registrationState !== "archived").map(card => <div key={card.templateId} className={`rounded-lg border p-3 ${selection?.pin.templateId === card.templateId ? "border-violet-500 bg-violet-50" : "border-slate-200"}`}>
        {card.previewUrl ? <img src={resolveBackendAssetUrl(card.previewUrl)} alt={`${card.name} 원본 미리보기`} className="mb-2 aspect-video w-full rounded object-contain" /> : null}
        <p className="text-sm font-medium">{card.name}</p><p className="mt-1 text-xs text-slate-500">내 템플릿 · {card.kind === "framed" ? "양식 포함" : "스타일"} · {stateLabels[card.registrationState]}</p>
        <p className="mt-1 text-xs text-slate-500">글꼴 상태: {templateFontText(card.fontState)}</p>
        <div className="mt-2 flex gap-2">{card.registrationState === "ready" ? <Button type="button" variant="outline" size="sm" aria-pressed={selection?.pin.templateId === card.templateId} onClick={() => choose(card)}>선택</Button> : <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void openDraft(card.templateId)}>분석 결과 확인</Button>}</div>
      </div>)}
    </div>
    {selection ? <div className="space-y-3 rounded-lg bg-slate-50 p-3">
      <div className="flex items-center justify-between gap-2"><p className="text-sm font-medium">{selection.name}</p><Button type="button" variant="ghost" size="sm" onClick={() => dispatch(setPptGenUploadState({ svgTemplateSelection: null }))}>선택 해제</Button></div>
      {selection.kind === "framed" ? <fieldset className="space-y-2"><legend className="mb-2 text-sm font-medium">생성 방식</legend>{(["template-redesign", "free-design"] as const).map(mode => <label key={mode} className="flex gap-2 text-sm"><input type="radio" name="svg-template-mode" value={mode} checked={selection.mode === mode} disabled={available !== true || (selected ? !selected.supportedModes.includes(mode) : true)} onChange={() => changeMode(mode)} /><span>{modeLabels[mode]}<span className="mt-1 block text-xs text-slate-500">{modeDescriptions[mode]}</span></span></label>)}</fieldset> : null}
      <p role="status" className={`text-xs ${capability?.state === "available" ? "text-emerald-700" : "text-amber-800"}`}>{!capability ? "사용 가능 여부 확인 중" : capability.state === "available" ? "선택한 크기로 생성할 수 있습니다." : capability.state === "preparation-required" ? "이 크기의 양식 준비가 필요합니다." : "현재 설정으로 생성할 수 없습니다."}</p>
      {available === true && capability?.state === "preparation-required" ? <Button type="button" size="sm" variant="outline" disabled={busy || Boolean(preparation && ["queued", "running", "pending"].includes(preparation.state))} onClick={() => void prepare()}>이 크기의 양식 준비</Button> : null}
      {preparation ? <p role="status" className="text-xs text-slate-600">양식 준비: {templateOperationText(preparation.state)} {preparation.errorCode ? templateReason(preparation.errorCode) : ""}</p> : null}
      {capability && capability.state !== "available" ? <p className="text-xs text-slate-600">{templateReason(capability.reasonCode)} {templateActionText(capability.nextAction)}</p> : null}
    </div> : null}
    {available === true ? <details className="rounded-lg border border-slate-200 p-3"><summary className="cursor-pointer text-sm font-medium">내 템플릿 추가</summary>
      <p className="mt-2 text-xs text-slate-500">PPTX를 분석한 뒤 원본 양식과 글꼴을 확인하여 등록합니다. PDF와 이미지는 아래 임시 디자인 참조를 이용하세요.</p>
      <label className="mt-3 block text-sm">템플릿 이름<input className="mt-1 block w-full rounded border p-2" value={name} onChange={event => setName(event.target.value)} maxLength={160} /></label>
      <label className="mt-3 block text-sm">PPTX 파일<input className="mt-1 block w-full text-xs" type="file" accept=".pptx" onChange={event => { const next = event.target.files?.[0] ?? null; if (next && !/\.pptx$/i.test(next.name)) { notify.error("PPTX 파일을 선택해 주세요."); return; } setFile(next); if (next && !name) setName(next.name.replace(/\.pptx$/i, "")); }} /></label>
      <Button className="mt-3" type="button" size="sm" disabled={busy || !file || !name.trim()} onClick={() => void register()}>{busy ? "처리 중" : "분석 시작"}</Button>
    </details> : null}
    {available === true && draft ? <div className="space-y-3 rounded-lg border border-amber-200 p-3" aria-label="템플릿 분석 검토">
      <h4 className="text-sm font-semibold">{draft.template.name} · {stateLabels[draft.template.registrationState]}</h4><details className="text-xs text-slate-500"><summary className="cursor-pointer">작업 정보</summary><p>작업 ID: {draft.operationId}</p></details>
      <p className="text-xs">글꼴 준비 상태: {templateFontText(draft.template.fontState)}</p>{draft.errorCode ? <p role="alert" className="text-xs text-rose-700">{templateReason(draft.errorCode)}</p> : null}
      {[...(draft.template.issues ?? []), ...(draft.package?.issues ?? [])].map((issue, index) => <p className="text-xs text-amber-800" key={index}>{templateIssueText(issue)}</p>)}
      {(draft.reviewEvidence ?? draft.review_evidence)?.status === "blocked" ? <p role="alert" className="text-xs text-amber-800">{templateReason((draft.reviewEvidence ?? draft.review_evidence)?.reason)}</p> : null}
      {draft.package?.frames.map(frame => {
        const blockedReason = frameReviewBlockReason(draft, frame);
        return <div key={frame.frame_id} className="rounded border p-2 text-sm"><label className="flex items-center gap-2"><input type="checkbox" disabled={Boolean(blockedReason)} checked={!blockedReason && selectedFrames.includes(frame.frame_id)} onChange={event => { setConfirmed(false); setSelectedFrames(previous => event.target.checked ? [...previous, frame.frame_id] : previous.filter(id => id !== frame.frame_id)); }} /> {templateRoleText(frame.role)} · 원본 {(frame.source_page ?? 0) + 1}장</label>{framePreview(frame)}
          {(frame.issues ?? []).map((issue, index) => <p className="mt-1 text-xs text-amber-800" key={index}>{templateIssueText(issue)}</p>)}
          {blockedReason ? <p role="status" className="mt-1 text-xs text-amber-800">{blockedReason}</p> : null}
        </div>;
      })}
      <SvgTemplateReview key={`${draft.operationId}:${draft.package?.digest ?? "pending"}`} draft={draft} busy={busy} onDirtyChange={dirty => { setReviewDirty(dirty); setConfirmed(false); }} onSave={async (frames, representativeFrameId) => {
        if (!ownerAlive.current || available !== true) return;
        const owner = ownerEpoch.current;
        setBusy(true);
        try {
          const result = await SvgTemplatesApi.review(draft.template.templateId, { operationId: draft.operationId, expectedHeadRevision: draft.template.pin?.revision ?? 0, frameReviews: frames, representativeFrameId });
          if (!live(owner)) return;
          setReviewDirty(false);
          setDraft({ template: { ...draft.template, registrationState: "analyzing" }, operationId: result.operationId, package: null });
          setConfirmed(false); setRefresh(value => value + 1);
        } catch { if (live(owner)) notify.error("보존 영역 수정을 저장하지 못했습니다. 원본 분석 결과는 유지됩니다."); }
        finally { if (live(owner)) setBusy(false); }
      }} />
      {reviewDirty ? <p role="status" className="text-xs text-amber-800">수정 내용을 저장하고 다시 분석한 뒤 검토를 확인해 주세요.</p> : null}
      <p className="text-xs text-slate-500">파란 영역은 새 본문을 작성할 공간입니다. 원본과 보존할 양식을 비교하여 글꼴·글자 위치·줄 간격을 확인해 주세요. 잘못 분류된 보존 영역이나 누락된 글꼴은 준비 완료로 처리할 수 없습니다.</p>
      <label className="flex gap-2 text-xs"><input type="checkbox" checked={confirmed} disabled={reviewDirty} onChange={event => setConfirmed(event.target.checked)} />{draft.package?.frames.some(frame => !frameReviewBlockReason(draft, frame)) ? "두 미리보기의 글꼴과 요소 위치를 비교했으며, 표시된 양식으로 등록하겠습니다." : "분석 결과와 디자인 참조 등록 범위를 확인했습니다."}</label>
      <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void openDraft(draft.template.templateId)}>분석 상태 확인</Button><Button type="button" size="sm" disabled={busy || reviewDirty || !confirmed || !selectedFrames.length || selectedFrames.some(id => { const frame = draft.package?.frames.find(item => item.frame_id === id); return !frame || Boolean(frameReviewBlockReason(draft, frame)); })} onClick={() => void publish()}>선택한 양식 등록</Button>{draft.template.registrationState === "failed" || draft.template.registrationState === "review-required" ? <Button type="button" size="sm" variant="outline" disabled={busy || reviewDirty || !confirmed} onClick={() => void publish(true)}>디자인 참조로만 등록</Button> : null}<Button type="button" size="sm" variant="ghost" onClick={() => setDraft(null)}>닫기</Button></div>
    </div> : null}
  </section>;
}
