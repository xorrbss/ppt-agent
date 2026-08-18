"use client";

import { ClipboardEvent, DragEvent, FormEvent, useState } from "react";
import { Download, FileText, Loader2, Paperclip, X } from "lucide-react";
import { useTranslations } from "next-intl";
import type {
  ConversationAsset,
  ConversationContext,
  ConversationRunAction,
  ConversationTimeline,
} from "./types";

const actionMessageKey: Record<
  ConversationRunAction,
  | "action.retry"
  | "action.cancel"
  | "action.approve"
  | "action.reject"
  | "action.undo"
> = {
  retry: "action.retry",
  cancel: "action.cancel",
  approve: "action.approve",
  reject: "action.reject",
  undo: "action.undo",
};

const runMessageKey: Record<
  string,
  | "runType.presentationGenerate" | "runType.presentationEdit"
  | "runType.presentationDelete" | "runType.slideEdit"
  | "runType.slideRegenerate" | "runType.slideRegenerateBatch"
  | "runType.slideDelete" | "runType.versionRestore"
  | "runType.templateApply" | "runType.qualityReview"
  | "runType.exportPptx" | "runType.exportPdf"
> = {
  "presentation.generate": "runType.presentationGenerate",
  "presentation.edit": "runType.presentationEdit",
  "presentation.delete": "runType.presentationDelete",
  "slide.edit": "runType.slideEdit",
  "slide.regenerate": "runType.slideRegenerate",
  "slide.regenerate.batch": "runType.slideRegenerateBatch",
  "slide.delete": "runType.slideDelete",
  "version.restore": "runType.versionRestore",
  "template.apply": "runType.templateApply",
  "design.apply": "runType.templateApply",
  "quality.review": "runType.qualityReview",
  "export.pptx": "runType.exportPptx",
  "export.pdf": "runType.exportPdf",
};

const statusMessageKey: Record<
  string,
  | "status.queued"
  | "status.waiting"
  | "status.running"
  | "status.approval"
  | "status.cancelling"
  | "status.succeeded"
  | "status.failed"
  | "status.cancelled"
> = {
  queued: "status.queued",
  waiting_for_assets: "status.waiting",
  blocked: "status.waiting",
  running: "status.running",
  approval_required: "status.approval",
  awaiting_approval: "status.approval",
  cancelling: "status.cancelling",
  succeeded: "status.succeeded",
  failed: "status.failed",
  cancelled: "status.cancelled",
  dead_letter: "status.failed",
};

const statusClass: Record<string, string> = {
  queued: "bg-slate-100 text-slate-600",
  waiting_for_assets: "bg-amber-50 text-amber-700",
  blocked: "bg-amber-50 text-amber-700",
  running: "bg-blue-50 text-blue-700",
  approval_required: "bg-amber-50 text-amber-700",
  awaiting_approval: "bg-amber-50 text-amber-700",
  cancelling: "bg-slate-100 text-slate-600",
  succeeded: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-700",
  cancelled: "bg-slate-100 text-slate-600",
  dead_letter: "bg-red-50 text-red-700",
};

function formatBytes(size?: number): string | undefined {
  if (size === undefined || !Number.isFinite(size) || size < 0) {
    return undefined;
  }
  if (size < 1_024) return `${size} B`;
  if (size < 1_048_576) return `${(size / 1_024).toFixed(1)} KB`;
  if (size < 1_073_741_824) return `${(size / 1_048_576).toFixed(1)} MB`;
  return `${(size / 1_073_741_824).toFixed(1)} GB`;
}

function safeAssetUrl(value?: string): string | undefined {
  if (!value || value.startsWith("//")) return undefined;
  try {
    const parsed = new URL(value, "https://workspace.local");
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? value
      : undefined;
  } catch {
    return undefined;
  }
}

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
  const [draggingFiles, setDraggingFiles] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);

  const uploadFiles = async (files: File[]) => {
    if (!files.length || !onUpload || uploading) return;
    setAttachmentError(null);
    setUploading(true);
    try {
      await onUpload(files);
    } catch {
      setAttachmentError(t("uploadFailed"));
    } finally {
      setUploading(false);
    }
  };

  const hasDraggedFiles = (event: DragEvent<HTMLElement>) =>
    Array.from(event.dataTransfer.types || []).includes("Files");

  const handleDragEnter = (event: DragEvent<HTMLElement>) => {
    if (!onUpload || !hasDraggedFiles(event)) return;
    event.preventDefault();
    setDraggingFiles(true);
  };

  const handleDragOver = (event: DragEvent<HTMLElement>) => {
    if (!onUpload || !hasDraggedFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDraggingFiles(true);
  };

  const handleDragLeave = (event: DragEvent<HTMLElement>) => {
    if (
      event.relatedTarget instanceof Node &&
      event.currentTarget.contains(event.relatedTarget)
    ) {
      return;
    }
    setDraggingFiles(false);
  };

  const handleDrop = (event: DragEvent<HTMLElement>) => {
    if (!onUpload || !hasDraggedFiles(event)) return;
    event.preventDefault();
    setDraggingFiles(false);
    void uploadFiles(Array.from(event.dataTransfer.files ?? []));
  };

  const handlePaste = (event: ClipboardEvent<HTMLElement>) => {
    if (!onUpload) return;
    const images = Array.from(event.clipboardData.files ?? []).filter((file) =>
      file.type.startsWith("image/"),
    );
    if (!images.length) return;
    event.preventDefault();
    void uploadFiles(images);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const value = draft.trim();
    if (!value || submitting) return;
    await onSend(value);
    setDraft("");
  };

  const runTitle = (title?: string) => {
    const key = title ? runMessageKey[title] : undefined;
    return key ? t(key) : title || t("agentRun");
  };

  const runStatus = (status: string) => {
    const key = statusMessageKey[status];
    return key ? t(key) : status;
  };

  const renderAttachments = (
    assets: ConversationAsset[],
    appearance: "user" | "assistant",
  ) => (
    <ul className="mt-3 space-y-1.5" aria-label={t("attachments")}>
      {assets.map((asset) => {
        const downloadUrl = safeAssetUrl(asset.downloadUrl);
        const size = formatBytes(asset.sizeBytes);
        return (
          <li
            key={asset.id}
            className={`flex min-w-0 items-center gap-2 rounded-lg border px-2.5 py-2 ${
              appearance === "user"
                ? "border-white/20 bg-white/10"
                : "border-slate-200 bg-white"
            }`}
            data-testid={`conversation-attachment-${asset.id}`}
          >
            <FileText className="h-4 w-4 shrink-0 opacity-80" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium" title={asset.name}>
                {asset.name}
              </p>
              {(asset.mimeType || size) && (
                <p
                  className={`mt-0.5 truncate text-[10px] ${
                    appearance === "user" ? "text-blue-100" : "text-slate-500"
                  }`}
                >
                  {[asset.mimeType, size].filter(Boolean).join(" · ")}
                </p>
              )}
            </div>
            {downloadUrl && (
              <a
                href={downloadUrl}
                download={asset.name}
                className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors ${
                  appearance === "user"
                    ? "bg-white/15 text-white hover:bg-white/25"
                    : "bg-blue-50 text-blue-700 hover:bg-blue-100"
                }`}
                aria-label={t("downloadAttachment", { name: asset.name })}
                title={t("download")}
                data-testid="conversation-attachment-download"
              >
                <Download className="h-3.5 w-3.5" aria-hidden="true" />
              </a>
            )}
          </li>
        );
      })}
    </ul>
  );

  return (
    <section
      className="relative flex min-h-0 flex-1 flex-col bg-white font-inter antialiased"
      aria-label={t("ariaLabel")}
      data-testid="conversation-surface"
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onPaste={handlePaste}
    >
      {draggingFiles && (
        <div
          className="pointer-events-none absolute inset-2 z-30 flex items-center justify-center rounded-2xl border-2 border-dashed border-blue-400 bg-blue-50/95 text-sm font-semibold text-blue-700 shadow-lg"
          data-testid="conversation-drop-overlay"
          role="status"
        >
          <span className="inline-flex items-center gap-2">
            <Paperclip className="h-4 w-4" aria-hidden="true" />
            {t("dropFiles")}
          </span>
        </div>
      )}
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5 text-xs text-slate-500">
        <span
          className="inline-flex items-center gap-1.5"
          data-testid="conversation-connection"
          role="status"
          aria-live="polite"
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              connection === "live" ? "bg-emerald-500" : "bg-amber-400"
            }`}
            aria-hidden="true"
          />
          {connection === "live"
            ? t("connection.live")
            : connection === "connecting"
              ? t("connection.connecting")
              : t("connection.reconnecting")}
        </span>
        {context?.smartSelection && (
          <span
            className="rounded-full bg-violet-100 px-2 py-1 font-medium text-violet-800"
            data-testid="smart-selection-badge"
          >
            {t("smartSelection")}
          </span>
        )}
      </div>

      <ol
        className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-slate-50/60 p-4"
        data-testid="conversation-timeline"
      >
        {loading && (
          <li
            role="status"
            className="flex items-center justify-center gap-2 py-6 text-sm text-slate-500"
          >
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            {t("loading")}
          </li>
        )}
        {timeline.items.map((item) => {
          if (item.kind === "message") {
            const isUser = item.message.role === "user";
            return (
              <li
                key={`message-${item.message.id}`}
                className={`rounded-2xl px-3.5 py-3 text-sm leading-6 shadow-sm ${
                  isUser
                    ? "ml-8 bg-blue-600 text-white"
                    : "mr-8 border border-slate-200 bg-white text-slate-800"
                }`}
              >
                <p className="whitespace-pre-wrap break-words">
                  {item.message.content}
                </p>
                {item.message.streaming && (
                  <p className="mt-1 text-xs opacity-70" role="status">
                    {t("streaming")}
                  </p>
                )}
                {item.message.attachments.length > 0 &&
                  renderAttachments(
                    item.message.attachments,
                    isUser ? "user" : "assistant",
                  )}
              </li>
            );
          }

          if (item.kind === "tool") {
            return (
              <li
                key={`tool-${item.tool.id}`}
                className="rounded-xl border border-slate-200 bg-white px-3.5 py-3 text-xs text-slate-600 shadow-sm"
                data-testid="tool-activity"
              >
                <div className="flex items-center justify-between gap-3">
                  <strong className="truncate font-semibold text-slate-800">
                    {runTitle(item.tool.name)}
                  </strong>
                  <span className="shrink-0 text-slate-500">
                    {item.tool.status === "started"
                      ? t("tool.working")
                      : item.tool.summary ?? t("tool.completed")}
                  </span>
                </div>
              </li>
            );
          }

          const actions = item.run.actions ?? [];
          return (
            <li
              key={`run-${item.run.id}`}
              className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
              data-testid="agent-run"
            >
              <div className="flex items-center justify-between gap-3">
                <strong className="min-w-0 truncate text-sm font-semibold text-slate-900">
                  {runTitle(item.run.title)}
                </strong>
                <span
                  className={`shrink-0 rounded-full px-2 py-1 text-[11px] font-medium ${
                    statusClass[item.run.status] ?? "bg-slate-100 text-slate-600"
                  }`}
                >
                  {runStatus(item.run.status)}
                </span>
              </div>
              {["approval_required", "awaiting_approval"].includes(
                item.run.status,
              ) && (
                <p
                  className="mt-2 text-xs leading-5 text-amber-800"
                  data-testid="operation-approval-alert"
                  role="alert"
                  aria-live="assertive"
                >
                  {t("approvalRequired")}
                </p>
              )}
              {item.run.error && (
                <p className="mt-2 text-xs leading-5 text-red-700" role="alert">
                  {item.run.error}
                </p>
              )}
              {actions.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {actions.map((action) => (
                    <button
                      key={action}
                      type="button"
                      data-testid={`run-action-${action}`}
                      disabled={submitting}
                      onClick={() => void onAction(item.run.id, action)}
                      className={`rounded-lg border px-2.5 py-1.5 text-xs font-medium transition-colors disabled:opacity-50 ${
                        action === "cancel" || action === "reject"
                          ? "border-red-200 text-red-700 hover:bg-red-50"
                          : "border-slate-300 text-slate-700 hover:bg-slate-50"
                      }`}
                    >
                      {t(actionMessageKey[action])}
                    </button>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ol>

      {(error || attachmentError) && (
        <p className="mx-3 rounded-lg bg-red-50 p-2.5 text-xs text-red-700" role="alert">
          {error || attachmentError}
        </p>
      )}

      <form
        className="border-t border-slate-200 bg-white p-3"
        onSubmit={(event) => void submit(event)}
      >
        {pendingAssets.length > 0 && (
          <ul
            className="mb-2 flex flex-wrap gap-2"
            aria-label={t("attachments")}
          >
            {pendingAssets.map((asset) => (
              <li
                key={asset.id}
                className="inline-flex min-w-0 items-center gap-1.5 rounded-lg bg-blue-50 px-2 py-1.5 text-xs text-blue-800"
              >
                <Paperclip className="h-3 w-3 shrink-0" aria-hidden="true" />
                <span className="max-w-48 truncate" title={asset.name}>
                  {asset.name}
                </span>
                <button
                  type="button"
                  onClick={() => onRemoveAsset(asset.id)}
                  aria-label={t("removeAttachment", { name: asset.name })}
                  className="rounded p-0.5 hover:bg-blue-100"
                >
                  <X className="h-3 w-3" aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={t("placeholder")}
          rows={3}
          disabled={submitting}
          className="w-full resize-none rounded-xl border border-slate-300 p-3 text-sm leading-6 text-slate-900 outline-none placeholder:text-slate-400 focus:border-blue-400 focus:ring-2 focus:ring-blue-100 disabled:bg-slate-50"
          data-testid="conversation-composer"
        />
        <div className="mt-2 flex items-center justify-between">
          <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-300 px-2.5 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50">
            <input
              className="sr-only"
              type="file"
              multiple
              disabled={!onUpload || uploading || submitting}
              onChange={(event) => {
                const files = Array.from(event.currentTarget.files ?? []);
                event.currentTarget.value = "";
                void uploadFiles(files);
              }}
            />
            {uploading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Paperclip className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {uploading ? t("uploading") : t("attach")}
          </label>
          <span className="mx-2 hidden flex-1 text-center text-[10px] text-slate-400 sm:block">
            {t("pasteImages")}
          </span>
          <button
            type="submit"
            disabled={submitting || !draft.trim()}
            className="rounded-lg bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-45"
          >
            {t("send")}
          </button>
        </div>
      </form>
    </section>
  );
}
