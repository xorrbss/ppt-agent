"use client";

import { actionMessageKey, runMessageKey, statusMessageKey, statusClass, formatBytes, safeAssetUrl } from "./chatSurfaceHelpers";
import { ClipboardEvent, DragEvent, FormEvent, useState } from "react";
import { ArrowUp, Download, FileText, Loader2, Paperclip, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { WorkspacePartialRecovery } from "../workspace/WorkspacePartialRecovery";
import type { WorkspaceOperationActionOptions } from "../workspace/types";
import { renderMarkdown } from "@/lib/safe-markdown";
import type {
  ConversationAsset,
  ConversationContext,
  ConversationRunAction,
  ConversationTimeline,
} from "./types";
import {
  ConversationComposerFrame,
  ConversationMessageFrame,
  conversationAssistantMarkdownClassName,
  conversationAttachmentClassName,
  conversationTimelineClassName,
} from "./ConversationPrimitives";

export function ChatSurface(props: {
  timeline: ConversationTimeline;
  loading: boolean;
  submitting: boolean;
  partialRecoveryEnabled?: boolean;
  error?: string | null;
  connection: "connecting" | "live" | "offline";
  pendingAssets: ConversationAsset[];
  context?: ConversationContext;
  onUpload?: (files: File[]) => Promise<void>;
  onRemoveAsset: (assetId: string) => void;
  onSend: (content: string) => Promise<void>;
  onAction: (runId: string, action: ConversationRunAction, options?: WorkspaceOperationActionOptions) => Promise<void>;
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
            className={conversationAttachmentClassName(appearance)}
            data-testid={`conversation-attachment-${asset.id}`}
          >
            <FileText className="h-4 w-4 shrink-0 opacity-80" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium" title={asset.name}>
                {asset.name}
              </p>
              {(asset.mimeType || size) && (
                <p
                  className="mt-0.5 truncate text-[10px] text-slate-500"
                >
                  {[asset.mimeType, size].filter(Boolean).join(" · ")}
                </p>
              )}
            </div>
            {downloadUrl && (
              <a
                href={downloadUrl}
                download={asset.name}
                className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-white text-blue-700 transition-colors hover:bg-blue-50"
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
      {connection !== "live" && (
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5 text-xs text-slate-500">
          <span
            className="inline-flex items-center gap-1.5"
            data-testid="conversation-connection"
            role="status"
            aria-live="polite"
          >
            <span
              className="h-1.5 w-1.5 rounded-full bg-amber-400"
              aria-hidden="true"
            />
            {connection === "connecting"
              ? t("connection.connecting")
              : t("connection.reconnecting")}
          </span>
        </div>
      )}

      <ol
        className={conversationTimelineClassName}
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
            const assistantHtml = isUser
              ? ""
              : renderMarkdown(item.message.content);
            return (
              <li
                key={`message-${item.message.id}`}
                data-testid={`conversation-message-${item.message.id}`}
              >
                <ConversationMessageFrame
                  appearance={isUser ? "user" : "assistant"}
                  data-testid={`conversation-message-frame-${item.message.id}`}
                >
                  {isUser ? (
                    <p className="whitespace-pre-wrap break-words text-[15px] leading-[1.6]">
                      {item.message.content}
                    </p>
                  ) : (
                    <div
                      className={conversationAssistantMarkdownClassName}
                      dangerouslySetInnerHTML={{ __html: assistantHtml }}
                    />
                  )}
                  {item.message.streaming && (
                    <p className="mt-1 text-xs text-slate-500" role="status">
                      {t("streaming")}
                    </p>
                  )}
                  {item.message.attachments.length > 0 &&
                    renderAttachments(
                      item.message.attachments,
                      isUser ? "user" : "assistant",
                    )}
                </ConversationMessageFrame>
              </li>
            );
          }

          if (item.kind === "tool") {
            return (
              <li
                key={`tool-${item.tool.id}`}
                className="border-l-2 border-slate-200 py-1 pl-3 text-xs text-slate-600"
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
              className="border-l-2 border-slate-200 py-1 pl-3"
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
              {props.partialRecoveryEnabled && item.run.operationType === "presentation.generate" && ["failed", "dead_letter"].includes(item.run.status) && <WorkspacePartialRecovery
                operationId={item.run.operationId ?? item.run.id} operationVersion={item.run.version}
                currentRevision={timeline.revision} disabled={submitting || loading}
                onAction={(operationId, action, options) => onAction(operationId, action, options)} />}
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
        className="shrink-0 bg-white px-3 pb-3 pt-2"
        onSubmit={(event) => void submit(event)}
      >
        <ConversationComposerFrame data-testid="conversation-composer-frame">
        {pendingAssets.length > 0 && (
          <ul
            className="mb-2 flex flex-wrap gap-2"
            aria-label={t("attachments")}
          >
            {pendingAssets.map((asset) => (
              <li
                key={asset.id}
                className="inline-flex min-w-0 items-center gap-1.5 rounded-lg bg-slate-100 px-2 py-1.5 text-xs text-slate-700"
              >
                <Paperclip className="h-3 w-3 shrink-0" aria-hidden="true" />
                <span className="max-w-48 truncate" title={asset.name}>
                  {asset.name}
                </span>
                <button
                  type="button"
                  onClick={() => onRemoveAsset(asset.id)}
                  aria-label={t("removeAttachment", { name: asset.name })}
                  className="rounded p-0.5 hover:bg-slate-200"
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
          rows={1}
          disabled={submitting}
          className="max-h-[180px] min-h-12 w-full resize-none overflow-y-auto border-0 bg-transparent px-1 py-2 text-sm leading-6 text-slate-900 outline-none placeholder:text-slate-400 disabled:text-slate-400"
          data-testid="conversation-composer"
        />
        <div className="mt-2 flex items-center justify-between">
          <label className="inline-flex h-10 w-10 cursor-pointer items-center justify-center rounded-full text-slate-600 transition-colors hover:bg-slate-100">
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
            <span className="sr-only">{uploading ? t("uploading") : t("attach")}</span>
          </label>
          <span className="mx-2 hidden flex-1 text-center text-[10px] text-slate-400 sm:block">
            {t("pasteImages")}
          </span>
          <button
            type="submit"
            disabled={submitting || !draft.trim()}
            aria-label={t("send")}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-blue-600 text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500"
          >
            <ArrowUp className="h-4 w-4" aria-hidden="true" />
            <span className="sr-only">{t("send")}</span>
          </button>
        </div>
        </ConversationComposerFrame>
      </form>
    </section>
  );
}
