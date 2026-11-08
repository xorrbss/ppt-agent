"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { useDispatch, useSelector } from "react-redux";
import { Button } from "@/components/ui/button";
import { notify } from "@/components/ui/sonner";
import type { RootState } from "@/store/store";
import { setPptGenUploadState, invalidateSvgTemplateSession } from "@/store/slices/presentationGenUpload";
import { resolveBackendAssetUrl } from "@/utils/api";
import { PresentationGenerationApi } from "../../services/api/presentation-generation";
import { SvgTemplatesApi, svgTemplateCanvas, type SvgTemplateCard, type SvgTemplateDraft, type SvgTemplateCapability, type SvgTemplateMode } from "../../services/api/svg-templates";
import { AUTH_SESSION_EXPIRED_EVENT } from "../../services/api/api-error-handler";
import { templateReason, templateIssueText, templateFontText, templateRoleText, templateOperationText, templateActionText, frameReviewBlockReason, rasterPreservationLines, frameHasChapterTab } from "../../services/api/svg-template-status";
import SvgTemplateReview from "./SvgTemplateReview";
import SvgTemplateFontSubstitutions from "./SvgTemplateFontSubstitutions";
import type { PresentationConfig } from "../type";
import { templateSourceCanvasSelector } from "../../services/api/svg-template-canvas";
import { beginEpochRequest } from "../utils/requestEpoch";

export default function SvgTemplatePicker({ config, onConfigChange, sourceContentMode, hasCompanyMaster }: {
  config: PresentationConfig; onConfigChange: (key: keyof PresentationConfig, value: unknown) => void;
  sourceContentMode: boolean; hasCompanyMaster: boolean;
}) {
  const dispatch = useDispatch();
  const t = useTranslations("svgTemplatePicker");
  const tUpload = useTranslations("uploadExtras");
  const stateLabels = { analyzing: t("stateAnalyzing"), "review-required": t("stateReviewRequired"), ready: t("stateReady"), failed: t("stateFailed"), archived: t("stateArchived") };
  const modeLabels = { "template-redesign": t("modeTemplateRedesign"), "free-design": t("modeFreeDesign") };
  const modeDescriptions = {
    "template-redesign": t("modeTemplateRedesignDescription"),
    "free-design": t("modeFreeDesignDescription"),
  };
  // Failures this component raises itself use these codes, so their text comes from the catalog, not the backend-reason mapper.
  const localReasonText = (code?: string | null) => code === "capability_check_failed" ? t("capabilityCheckFailed") : code === "operation_status_unknown" ? t("operationStatusUnknown") : templateReason(code);
  const storedSelection = useSelector((state: RootState) => state.pptGenUpload.svgTemplateSelection);
  const selection = useMemo(() => storedSelection ?? (config.svgTemplatePin && config.svgTemplateMode ? {
    pin: config.svgTemplatePin, mode: config.svgTemplateMode, name: config.svgTemplateName ?? t("savedTemplateName"), kind: config.svgTemplateKind ?? "framed" as const,
  } : null), [storedSelection, config.svgTemplatePin, config.svgTemplateMode, config.svgTemplateName, config.svgTemplateKind, t]);
  const [available, setAvailable] = useState<boolean | null>(null);
  const [cards, setCards] = useState<SvgTemplateCard[]>([]);
  const [pinnedCard, setPinnedCard] = useState<SvgTemplateCard | null>(null);
  const [error, setError] = useState<"" | "load_failed">("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<SvgTemplateDraft | null>(null);
  const currentDraft = useRef(draft);
  useEffect(() => { currentDraft.current = draft; }, [draft]);
  const [selectedFrames, setSelectedFrames] = useState<string[]>([]);
  const [reviewDirty, setReviewDirty] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [preparation, setPreparation] = useState<{ operationId: string; state: string; errorCode?: string } | null>(null);
  const [capability, setCapability] = useState<SvgTemplateCapability | null>(null);
  const capabilityEpoch = useRef(0);
  const ownerEpoch = useRef(0);
  const preparationEpoch = useRef(0);
  const draftEpoch = useRef(0);
  const ownerAlive = useRef(true);
  const [ownerInvalidated, setOwnerInvalidated] = useState(false);
  const live = (epoch: number) => ownerAlive.current && ownerEpoch.current === epoch;
  // Every draft request takes a new draft epoch. Close, a newer draft request or a session
  // change makes it stale: a stale response must not touch the draft panel, and a stale
  // request's completion must not release the busy state that a newer request holds.
  const beginDraftRequest = () => {
    const owner = ownerEpoch.current;
    return beginEpochRequest(draftEpoch, () => live(owner));
  };
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
  // A template-redesign deck is generated only at the template's own canvas.
  const lockedCanvas = selection?.mode === "template-redesign" && selected?.sourceCanvas ? templateSourceCanvasSelector(selected.sourceCanvas) : null;
  const lockedCanvasKey = JSON.stringify(lockedCanvas);
  useEffect(() => {
    if (!lockedCanvas) return;
    for (const [key, value] of Object.entries(lockedCanvas) as Array<[keyof PresentationConfig, unknown]>) {
      if (JSON.stringify(config[key]) !== JSON.stringify(value)) onConfigChange(key, value);
    }
  // The lock and the current request canvas are the only inputs.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockedCanvasKey, canvasKey]);

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
      .catch(() => { if (!cancelled && live(owner)) { setAvailable(false); setCards([]); setError("load_failed"); } })
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
      setCapability({ state: "blocked", reasonCode: "capability_check_failed", nextAction: null, contractDigest: "" });
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
      }).catch(() => { if (live(owner) && revision === preparationEpoch.current) setPreparation({ ...preparation, state: "unknown", errorCode: "operation_status_unknown" }); });
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [preparation]);
  // A draft that is still being analyzed has no result to show yet: follow it until the server leaves "analyzing".
  const analyzingTemplateId = draft?.template.registrationState === "analyzing" ? draft.template.templateId : null;
  useEffect(() => {
    if (!analyzingTemplateId) return;
    const owner = ownerEpoch.current;
    if (!live(owner)) return;
    const epoch = draftEpoch.current;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      SvgTemplatesApi.draft(analyzingTemplateId).then(result => {
        if (cancelled || !live(owner) || epoch !== draftEpoch.current
          || result.template.templateId !== analyzingTemplateId || result.operationId !== draft?.operationId) return;
        setDraft(result);
        if (result.template.registrationState === "analyzing") return;
        setReviewDirty(false); setConfirmed(false); setRefresh(value => value + 1);
        setSelectedFrames(result.package?.frames.filter(frame => !frameReviewBlockReason(result, frame)).map(frame => frame.frame_id) ?? []);
      }).catch(() => { /* keep the manual "분석 상태 확인" button as the fallback */ });
    }, 3000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [analyzingTemplateId, draft]);
  const prepare = async () => {
    if (!selection || busy || !ownerAlive.current || available !== true) return;
    const owner = ownerEpoch.current;
    setBusy(true);
    const revision = preparationEpoch.current;
    try { const result = await SvgTemplatesApi.prepare({ templatePin: selection.pin, canvas }); if (live(owner) && revision === preparationEpoch.current) setPreparation(result); }
    catch { if (live(owner)) notify.error(t("prepareFailed")); }
    finally { if (live(owner)) setBusy(false); }
  };
  const choose = (card: SvgTemplateCard) => {
    if (!ownerAlive.current || available !== true || !card.pin || card.registrationState !== "ready") return;
    setPreparation(null);
    dispatch(setPptGenUploadState({ svgTemplateSelection: { pin: card.pin, mode: card.defaultMode, name: card.name, kind: card.kind } }));
    onConfigChange("svgDesignReferenceSnapshotId", undefined);
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
    const current = beginDraftRequest();
    setBusy(true);
    try { const result = await SvgTemplatesApi.draft(id); if (!current()) return; setDraft(result); setReviewDirty(false); setSelectedFrames(result.package?.frames.filter(frame => !frameReviewBlockReason(result, frame)).map(frame => frame.frame_id) ?? []); setConfirmed(false); }
    catch { if (current()) notify.error(t("openDraftFailed")); }
    finally { if (current()) setBusy(false); }
  };
  const register = async () => {
    if (!file || !name.trim() || busy || !ownerAlive.current || available !== true) return;
    const current = beginDraftRequest();
    setBusy(true);
    try {
      const admission = await PresentationGenerationApi.uploadDoc([file], `svg-template:${crypto.randomUUID()}`);
      if (!current()) return;
      const result = await SvgTemplatesApi.register({ name: name.trim(), referenceSnapshotId: admission.referenceSnapshotId, registrationMode: "framed" });
      if (!current()) return;
      setReviewDirty(false);
      setDraft({ template: result.template, operationId: result.operationId, package: null });
      setRefresh(value => value + 1); setConfirmed(false);
    } catch { if (current()) notify.error(t("registerFailed")); }
    finally { if (current()) setBusy(false); }
  };
  const framePreview = (frame: NonNullable<SvgTemplateDraft["package"]>["frames"][number]) => {
    const url = frame.preview_url || draft?.source_previews?.[String(frame.source_page)];
    if (!url) return <p className="mt-2 text-xs text-amber-800">{t("sourcePreviewMissing")}</p>;
    const source = frame.source_canvas?.logicalSize;
    const region = frame.body_region;
    const frameUrl = draft?.source_previews?.[`frame:${frame.frame_id}`];
    return <div className="mt-2 grid gap-3 sm:grid-cols-2"><div><p className="mb-1 text-xs font-medium">{t("sourceSlide")}</p><div className="relative inline-block max-w-full">
      <img src={resolveBackendAssetUrl(url)} alt={t("sourceSlideAlt", { page: (frame.source_page ?? 0) + 1 })} className="block max-h-64 max-w-full object-contain" />
      {source && region ? <span aria-label={t("bodyRegionAria")} className="pointer-events-none absolute border-2 border-blue-500 bg-blue-400/10" style={{ left: `${region.x / source.width * 100}%`, top: `${region.y / source.height * 100}%`, width: `${region.width / source.width * 100}%`, height: `${region.height / source.height * 100}%` }} /> : null}
    </div></div><div><p className="mb-1 text-xs font-medium">{t("preservedFrame")}</p>{frameUrl ? <img src={resolveBackendAssetUrl(frameUrl)} alt={t("preservedFrameAlt")} className="block max-h-64 max-w-full object-contain" /> : <p className="text-xs text-amber-800">{t("framePreviewMissing")}</p>}</div></div>;
  };
  const publish = async (styleOnly = false) => {
    if (!draft || busy || !confirmed || reviewDirty || !ownerAlive.current || available !== true) return;
    if (!styleOnly && (!selectedFrames.length || selectedFrames.some(id => {
      const frame = draft.package?.frames.find(item => item.frame_id === id);
      return !frame || Boolean(frameReviewBlockReason(draft, frame));
    }))) return;
    const owner = ownerEpoch.current;
    const epoch = draftEpoch.current;
    const templateId = draft.template.templateId;
    const operationId = draft.operationId;
    const current = () => live(owner) && epoch === draftEpoch.current
      && currentDraft.current?.template.templateId === templateId
      && currentDraft.current?.operationId === operationId;
    setBusy(true);
    try {
      const result = await SvgTemplatesApi.publish(draft.template.templateId, { operationId: draft.operationId, expectedHeadRevision: draft.template.pin?.revision ?? 0, selectedFrameIds: selectedFrames, confirmReview: confirmed, ...(styleOnly ? { styleOnly: true } : {}) });
      if (!current()) return;
      setBusy(false);
      setDraft(null); setRefresh(value => value + 1); if (result.template) choose(result.template);
    } catch { if (current()) { setBusy(false); notify.error(t("publishFailed")); } }
  };

  if (ownerInvalidated) return <p role="status" className="mt-4 text-sm text-slate-500">{t("sessionChanged")}</p>;
  if (available === false && !selection) return null;
  return <section className="mt-4 space-y-4 rounded-xl border border-slate-200 p-4" aria-label={t("sectionTitle")}>
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold">{t("sectionTitle")}</h3><Button type="button" size="sm" variant="ghost" onClick={() => setRefresh(value => value + 1)}>{t("refresh")}</Button></div>
    {loading ? <p role="status" className="text-xs text-slate-500">{t("loading")}</p> : null}
    {error ? <p role="alert" className="text-sm text-rose-700">{t("loadFailed")}</p> : null}
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {cards.filter(card => card.provider === "user" && card.registrationState !== "archived").map(card => <div key={card.templateId} className={`rounded-lg border p-3 ${selection?.pin.templateId === card.templateId ? "border-violet-500 bg-violet-50" : "border-slate-200"}`}>
        {card.previewUrl ? <img src={resolveBackendAssetUrl(card.previewUrl)} alt={t("cardPreviewAlt", { name: card.name })} className="mb-2 aspect-video w-full rounded object-contain" /> : null}
        <p className="text-sm font-medium">{card.name}</p><p className="mt-1 text-xs text-slate-500">{t("cardMeta", { kind: card.kind === "framed" ? t("kindFramed") : t("kindStyle"), state: stateLabels[card.registrationState] })}</p>
        <p className="mt-1 text-xs text-slate-500">{t("fontState", { state: templateFontText(card.fontState) })}</p>
        <div className="mt-2 flex gap-2">{card.registrationState === "ready" ? <Button type="button" variant="outline" size="sm" aria-pressed={selection?.pin.templateId === card.templateId} onClick={() => choose(card)}>{t("choose")}</Button> : <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void openDraft(card.templateId)}>{t("openAnalysis")}</Button>}</div>
      </div>)}
    </div>
    {selection ? <div className="space-y-3 rounded-lg bg-slate-50 p-3">
      <div className="flex items-center justify-between gap-2"><p className="text-sm font-medium">{selection.name}</p><Button type="button" variant="ghost" size="sm" onClick={() => dispatch(setPptGenUploadState({ svgTemplateSelection: null }))}>{t("clearSelection")}</Button></div>
      {selection.kind === "framed" ? <fieldset className="space-y-2"><legend className="mb-2 text-sm font-medium">{t("generationMode")}</legend>{(["template-redesign", "free-design"] as const).map(mode => <label key={mode} className="flex gap-2 text-sm"><input type="radio" name="svg-template-mode" value={mode} checked={selection.mode === mode} disabled={available !== true || (selected ? !selected.supportedModes.includes(mode) : true)} onChange={() => changeMode(mode)} /><span>{modeLabels[mode]}<span className="mt-1 block text-xs text-slate-500">{modeDescriptions[mode]}</span></span></label>)}</fieldset> : null}
      <p role="status" className={`text-xs ${capability?.state === "available" ? "text-emerald-700" : "text-amber-800"}`}>{!capability ? t("capabilityChecking") : capability.state === "available" ? t("capabilityAvailable") : capability.state === "preparation-required" ? t("capabilityPreparationRequired") : t("capabilityBlocked")}</p>
      {available === true && capability?.state === "preparation-required" ? <Button type="button" size="sm" variant="outline" disabled={busy || Boolean(preparation && ["queued", "running", "pending"].includes(preparation.state))} onClick={() => void prepare()}>{t("prepareCanvas")}</Button> : null}
      {preparation ? <p role="status" className="text-xs text-slate-600">{t("preparationStatus", { state: templateOperationText(preparation.state), reason: preparation.errorCode ? localReasonText(preparation.errorCode) : "" })}</p> : null}
      {capability && capability.state !== "available" ? <p className="text-xs text-slate-600">{localReasonText(capability.reasonCode)} {capability.reasonCode === "capability_check_failed" ? null : templateActionText(capability.nextAction)}</p> : null}
    </div> : null}
    {available === true ? <details className="rounded-lg border border-slate-200 p-3"><summary className="cursor-pointer text-sm font-medium">{t("addTemplate")}</summary>
      <p className="mt-2 text-xs text-slate-500">{t("addTemplateHint")}</p>
      <label className="mt-3 block text-sm">{t("templateName")}<input className="mt-1 block w-full rounded border p-2" value={name} onChange={event => setName(event.target.value)} maxLength={160} /></label>
      <label className="mt-3 block text-sm">{t("pptxFile")}<input className="mt-1 block w-full text-xs" type="file" accept=".pptx" onChange={event => { const next = event.target.files?.[0] ?? null; if (next && !/\.pptx$/i.test(next.name)) { notify.error(t("pptxRequired")); return; } setFile(next); if (next && !name) setName(next.name.replace(/\.pptx$/i, "")); }} /></label>
      <Button className="mt-3" type="button" size="sm" disabled={busy || !file || !name.trim()} onClick={() => void register()}>{busy ? t("processing") : t("startAnalysis")}</Button>
    </details> : null}
    {available === true && draft ? <div className="space-y-3 rounded-lg border border-amber-200 p-3" aria-label={t("reviewAria")}>
      <h4 className="text-sm font-semibold">{draft.template.name} · {stateLabels[draft.template.registrationState]}</h4><details className="text-xs text-slate-500"><summary className="cursor-pointer">{t("jobInfo")}</summary><p>{t("jobId", { id: draft.operationId })}</p></details>
      <p className="text-xs">{t("fontPreparation", { state: templateFontText(draft.template.fontState) })}</p>{draft.errorCode ? <p role="alert" className="text-xs text-rose-700">{templateReason(draft.errorCode)}</p> : null}
      {[...(draft.template.issues ?? []), ...(draft.package?.issues ?? [])].map((issue, index) => <p className="text-xs text-amber-800" key={index}>{templateIssueText(issue)}</p>)}
      {(draft.reviewEvidence ?? draft.review_evidence)?.renderer?.approximate ? <p role="status" className="text-xs text-amber-800">{tUpload("approximateSourcePreview")}</p> : null}
      {(draft.reviewEvidence ?? draft.review_evidence)?.status === "blocked" ? <p role="alert" className="text-xs text-amber-800">{templateReason((draft.reviewEvidence ?? draft.review_evidence)?.reason)}</p> : null}
      {draft.package?.frames.map(frame => {
        const blockedReason = frameReviewBlockReason(draft, frame);
        return <div key={frame.frame_id} className="rounded border p-2 text-sm"><label className="flex items-center gap-2"><input type="checkbox" disabled={Boolean(blockedReason)} checked={!blockedReason && selectedFrames.includes(frame.frame_id)} onChange={event => { setConfirmed(false); setSelectedFrames(previous => event.target.checked ? [...previous, frame.frame_id] : previous.filter(id => id !== frame.frame_id)); }} /> {templateRoleText(frame.role)} · {t("sourcePage", { page: (frame.source_page ?? 0) + 1 })}</label>{framePreview(frame)}
          {(frame.issues ?? []).map((issue, index) => <p className="mt-1 text-xs text-amber-800" key={index}>{templateIssueText(issue)}</p>)}
          {rasterPreservationLines(frame).map((line, index) => <p className="mt-1 text-xs text-slate-600" key={`raster-${index}`}>{line}</p>)}
          {frameHasChapterTab(frame) ? <p className="mt-1 text-xs text-slate-600">{tUpload("chapterTabFilled")}</p> : null}
          {blockedReason ? <p role="status" className="mt-1 text-xs text-amber-800">{blockedReason}</p> : null}
        </div>;
      })}
      <SvgTemplateFontSubstitutions key={`fonts:${draft.operationId}:${draft.package?.digest ?? "pending"}`} draft={draft} busy={busy || reviewDirty} onApply={async fontSubstitutions => {
        if (!ownerAlive.current || available !== true) return false;
        const current = beginDraftRequest();
        setBusy(true);
        try {
          const result = await SvgTemplatesApi.review(draft.template.templateId, { operationId: draft.operationId, expectedHeadRevision: draft.template.pin?.revision ?? 0, frameReviews: [], fontSubstitutions });
          if (!current()) return false;
          setDraft({ template: { ...draft.template, registrationState: "analyzing" }, operationId: result.operationId, package: null });
          setConfirmed(false); setRefresh(value => value + 1);
          return true;
        } catch (error) { if (current()) throw error; return false; }
        finally { if (current()) setBusy(false); }
      }} />
      <SvgTemplateReview key={`${draft.operationId}:${draft.package?.digest ?? "pending"}`} draft={draft} busy={busy} onDirtyChange={dirty => { setReviewDirty(dirty); setConfirmed(false); }} onSave={async (frames, representativeFrameId) => {
        if (!ownerAlive.current || available !== true) return;
        const current = beginDraftRequest();
        setBusy(true);
        try {
          const result = await SvgTemplatesApi.review(draft.template.templateId, { operationId: draft.operationId, expectedHeadRevision: draft.template.pin?.revision ?? 0, frameReviews: frames, representativeFrameId });
          if (!current()) return;
          setReviewDirty(false);
          setDraft({ template: { ...draft.template, registrationState: "analyzing" }, operationId: result.operationId, package: null });
          setConfirmed(false); setRefresh(value => value + 1);
        } catch { if (current()) notify.error(t("reviewSaveFailed")); }
        finally { if (current()) setBusy(false); }
      }} />
      {reviewDirty ? <p role="status" className="text-xs text-amber-800">{t("reviewDirty")}</p> : null}
      <p className="text-xs text-slate-500">{t("reviewGuide")}</p>
      <label className="flex gap-2 text-xs"><input type="checkbox" checked={confirmed} disabled={reviewDirty} onChange={event => setConfirmed(event.target.checked)} />{draft.package?.frames.some(frame => !frameReviewBlockReason(draft, frame)) ? t("confirmCompared") : t("confirmReference")}</label>
      <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void openDraft(draft.template.templateId)}>{t("checkStatus")}</Button><Button type="button" size="sm" disabled={busy || reviewDirty || !confirmed || !selectedFrames.length || selectedFrames.some(id => { const frame = draft.package?.frames.find(item => item.frame_id === id); return !frame || Boolean(frameReviewBlockReason(draft, frame)); })} onClick={() => void publish()}>{t("registerSelected")}</Button>{draft.template.registrationState === "failed" || draft.template.registrationState === "review-required" ? <Button type="button" size="sm" variant="outline" disabled={busy || reviewDirty || !confirmed} onClick={() => void publish(true)}>{t("registerReferenceOnly")}</Button> : null}<Button type="button" size="sm" variant="ghost" onClick={() => { draftEpoch.current += 1; setDraft(null); setBusy(false); }}>{t("close")}</Button></div>
    </div> : null}
  </section>;
}
