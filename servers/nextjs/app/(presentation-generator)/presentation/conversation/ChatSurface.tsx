"use client";

import { FormEvent, useState } from "react";
import { useTranslations } from "next-intl";
import type {
  ConversationAsset,
  ConversationContext,
  ConversationRunAction,
  ConversationTimeline,
} from "./types";

const actionMessageKey: Record<
  ConversationRunAction,
  "action.retry" | "action.cancel" | "action.approve" | "action.reject" | "action.undo"
> = {
  retry: "action.retry",
  cancel: "action.cancel",
  approve: "action.approve",
  reject: "action.reject",
  undo: "action.undo",
};

export function ChatSurface(props: {
  timeline: ConversationTimeline;
  loading: boolean;
  submitting: boolean;
  error?: string | null;
  connection: "connecting" | "live" | "offline";
  pendingAssets: ConversationAsset[];
  context?: ConversationContext;
  onUpload?: (files: File[]) => Promise<void>;
  onRemoveAsset: (assetId: string) => void;
  onSend: (content: string) => Promise<void>;
  onAction: (runId: string, action: ConversationRunAction) => Promise<void>;
}) {
  const t = useTranslations("conversation");
  const {
    timeline,
    loading,
    submitting,
    error,
    connection,
    pendingAssets,
    context,
    onUpload,
    onRemoveAsset,
    onSend,
    onAction,
  } = props;
  const [draft, setDraft] = useState("");
  const [uploading, setUploading] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = draft.trim();
    if (!value || submitting) return;
    await onSend(value);
    setDraft("");
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col bg-white" aria-label={t("ariaLabel")} data-testid="conversation-surface">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2 text-xs text-slate-500">
        <span data-testid="conversation-connection" role="status" aria-live="polite">
          {connection === "live"
            ? t("connection.live")
            : connection === "connecting"
              ? t("connection.connecting")
              : t("connection.reconnecting")}
        </span>
        {context?.smartSelection && (
          <span className="rounded-full bg-violet-100 px-2 py-1 font-medium text-violet-800" data-testid="smart-selection-badge">
            {t("smartSelection")}
          </span>
        )}
      </div>
      <ol className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3" data-testid="conversation-timeline">
        {loading && <li role="status" className="text-sm text-slate-500">{t("loading")}</li>}
        {timeline.items.map((item) => {
          if (item.kind === "message") {
            return (
              <li key={`message-${item.message.id}`} className={item.message.role === "user" ? "ml-8 rounded-xl bg-blue-600 px-3 py-2 text-sm text-white" : "mr-8 rounded-xl bg-slate-100 px-3 py-2 text-sm text-slate-800"}>
                <p>{item.message.content}</p>
                {item.message.streaming && <p className="mt-1 text-xs opacity-70" role="status">{t("streaming")}</p>}
                {item.message.attachments.length > 0 && <ul className="mt-2 flex flex-wrap gap-1">{item.message.attachments.map((asset) => <li key={asset.id} className="rounded bg-white/20 px-1.5 py-0.5 text-xs">{asset.name}</li>)}</ul>}
              </li>
            );
          }
          if (item.kind === "tool") {
            return <li key={`tool-${item.tool.id}`} className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600" data-testid="tool-activity"><strong>{item.tool.name}</strong> {item.tool.status === "started" ? t("tool.working") : item.tool.summary ?? t("tool.completed")}</li>;
          }
          const actions = item.run.actions ?? [];
          return (
            <li key={`run-${item.run.id}`} className="rounded-xl border border-slate-200 bg-slate-50 p-3" data-testid="agent-run">
              <div className="flex items-center justify-between gap-2"><strong className="text-sm text-slate-800">{item.run.title ?? t("agentRun")}</strong><span className="text-xs text-slate-500">{item.run.status}</span></div>
              {["approval_required", "awaiting_approval"].includes(item.run.status) && <p className="mt-1 text-xs text-amber-800" data-testid="operation-approval-alert" role="alert" aria-live="assertive">{t("approvalRequired")}</p>}
              {item.run.error && <p className="mt-2 text-xs text-red-700" role="alert">{item.run.error}</p>}
              <div className="mt-2 flex flex-wrap gap-2">{actions.map((action) => <button key={action} type="button" data-testid={`run-action-${action}`} disabled={submitting} onClick={() => void onAction(item.run.id, action)} className={action === "cancel" ? "rounded border border-red-300 px-2 py-1 text-xs text-red-700 disabled:opacity-50" : "rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 disabled:opacity-50"}>{t(actionMessageKey[action])}</button>)}</div>
            </li>
          );
        })}
      </ol>
      {error && <p className="mx-3 rounded bg-red-50 p-2 text-xs text-red-700" role="alert">{error}</p>}
      <form className="border-t border-slate-200 p-3" onSubmit={(event) => void submit(event)}>
        {pendingAssets.length > 0 && <ul className="mb-2 flex flex-wrap gap-2" aria-label={t("attachments")}>{pendingAssets.map((asset) => <li key={asset.id} className="rounded bg-blue-50 px-2 py-1 text-xs text-blue-800">{asset.name} <button type="button" onClick={() => onRemoveAsset(asset.id)} aria-label={t("removeAttachment", { name: asset.name })}>×</button></li>)}</ul>}
        <textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={t("placeholder")} rows={3} disabled={submitting} className="w-full resize-none rounded-lg border border-slate-300 p-2 text-sm" data-testid="conversation-composer" />
        <div className="mt-2 flex items-center justify-between"><label className="cursor-pointer rounded border border-slate-300 px-2 py-1 text-xs text-slate-700"><input className="sr-only" type="file" multiple disabled={!onUpload || uploading || submitting} onChange={(event) => { const files = Array.from(event.currentTarget.files ?? []); event.currentTarget.value = ""; if (!files.length || !onUpload) return; setUploading(true); void onUpload(files).finally(() => setUploading(false)); }} />{uploading ? t("uploading") : t("attach")}</label><button type="submit" disabled={submitting || !draft.trim()} className="rounded bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">{t("send")}</button></div>
      </form>
    </section>
  );
}
