"use client";

import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ArrowUp, FileText, Loader2 } from "lucide-react";
import { useGenerationWorkspace, useWorkspaceComposerContinuity, useWorkspaceDraft } from "./GenerationWorkspaceProvider";

interface Props {
  presentationId?: string | null;
  status: string;
  busy?: boolean;
  progressLabel?: string;
  detail?: string;
  actions?: React.ReactNode;
  children?: React.ReactNode;
}

/** A conversation surface; progress is supplied only by the existing operation. */
export function GenerationPreparation({ presentationId, status, busy = true, progressLabel, detail, actions, children }: Props) {
  const t = useTranslations("a2cGenerationPreparation");
  const locale = useLocale();
  const workspace = useGenerationWorkspace();
  const setPreparationVisible = workspace?.setPreparationVisible;
  useLayoutEffect(() => {
    setPreparationVisible?.(true);
    return () => setPreparationVisible?.(false);
  }, [setPreparationVisible]);
  const { draft, setDraft } = useWorkspaceDraft(presentationId);
  const { composerRef, onFocus, onBlur } = useWorkspaceComposerContinuity(presentationId);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const key = presentationId ?? workspace?.session?.presentationId ?? "pending";
  const views = workspace?.views;
  useLayoutEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = views?.current[key]?.scrollTop ?? 0;
  }, [key, views]);
  const session = workspace?.session;
  const matching = session && (!presentationId || session.presentationId === presentationId) ? session : null;
  const [expanded, setExpanded] = useState(false);
  const [lastChange, setLastChange] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { setLastChange(Date.now()); }, [status, progressLabel]);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15_000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <section className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-white" data-testid="generation-conversation">
      <header className="shrink-0 border-b border-slate-100 px-5 py-4 text-sm font-medium text-slate-700">{t("title")}</header>
      <div ref={scrollRef} onScroll={(event) => { if (views) views.current[key] = { ...views.current[key], scrollTop: event.currentTarget.scrollTop }; }} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-8 sm:px-8">
        <div className="mx-auto w-full max-w-[800px] space-y-8">
          {matching && <div className="ml-auto w-fit max-w-[85%] rounded-3xl bg-slate-100 px-5 py-4 text-sm leading-7 text-slate-800">
            {matching.prompt ? <>
              <p className={expanded ? "whitespace-pre-wrap break-words" : "line-clamp-6 whitespace-pre-wrap break-words"}>{matching.prompt}</p>
              {matching.prompt.length > 300 && <button type="button" className="mt-2 text-xs text-slate-500 underline" onClick={() => setExpanded((value) => !value)}>{expanded ? t("collapse") : t("expandRequest")}</button>}
            </> : <p>{t("fromAttachments")}</p>}
            {matching.files.slice(0, 2).map((file, index) => <p key={`${file.name}-${index}`} className="mt-2 flex items-center gap-2 break-all text-xs text-slate-600"><FileText className="h-3.5 w-3.5 shrink-0" />{file.name}</p>)}
            {matching.files.length > 2 && <details className="mt-2 text-xs text-slate-600"><summary className="cursor-pointer">{t("moreFiles", { count: matching.files.length - 2 })}</summary>{matching.files.slice(2).map((file, index) => <p key={`${file.name}-${index}`} className="mt-2 break-all">{file.name}</p>)}</details>}
            <p className="mt-3 text-xs text-slate-500">{matching.settings.join(" · ")}</p>
          </div>}
          <div className="space-y-4 text-sm leading-7 text-slate-800">
            <div role="status" aria-live="polite" className="flex items-start gap-3">
              {busy && <Loader2 aria-hidden="true" className="mt-1 h-4 w-4 shrink-0 animate-spin text-slate-500 motion-reduce:animate-none" />}
              <div><p>{status}</p>{progressLabel && <p className="text-xs text-slate-500">{progressLabel}</p>}</div>
            </div>
            {detail && <details className="text-xs text-slate-500"><summary className="cursor-pointer">{t("showProcess")}</summary><p className="mt-2 whitespace-pre-wrap">{detail}</p></details>}
            {busy && now - lastChange >= 60_000 && <p className="text-xs text-slate-500">{t("stalled", { time: new Date(lastChange).toLocaleTimeString(locale === "ko" ? "ko-KR" : "en-US") })}</p>}
            {children}
            {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
          </div>
        </div>
      </div>
      <div className="shrink-0 bg-white px-4 pb-4 pt-3 sm:px-8">
        <div className="mx-auto w-full max-w-[800px] rounded-3xl border border-slate-200 bg-slate-50 px-4 py-3 focus-within:border-slate-400">
          <textarea ref={composerRef} id="workspace-message-input" aria-label={t("revisionRequest")} onFocus={onFocus} onBlur={onBlur} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={t("revisionPlaceholder")} rows={2} className="max-h-40 min-h-12 w-full resize-none bg-transparent text-sm leading-6 outline-none [field-sizing:content]" />
          <div className="flex items-center justify-between gap-3"><p className="text-xs text-slate-500">{t("sendAfterCompletion")}</p><button type="button" disabled aria-label={t("sendWhenComplete")} className="rounded-full bg-slate-200 p-2 text-slate-400"><ArrowUp className="h-4 w-4" /></button></div>
        </div>
      </div>
    </section>
  );
}
